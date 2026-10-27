"""Treat Issue text only as data; use the GitHub REST API, never shell commands."""

import argparse
import base64
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from validate import ROOT, reject_constant, unique_object


LABELS = {
    "preset-submission": ("1d76db", "Device preset submission"),
    "needs-fix": ("d73a4a", "Submission failed validation"),
    "validated": ("0e8a16", "Latest submission passed validation"),
    "approved-for-pr": ("5319e7", "Maintainer approval to create a preset PR"),
    "pr-created": ("0052cc", "A preset PR was created; manual review required"),
    "pr-stale": ("fbca04", "Issue changed after PR creation; review its snapshot"),
    "pr-error": ("b60205", "PR creation failed; see Bot comment and Actions logs"),
}
ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CONTRIBUTOR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
RESERVED_IDS = {"example", "con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


class SubmissionError(ValueError):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


def parse_json(text):
    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, RecursionError) as error:
        raise SubmissionError(f"JSON 无法解析：{error}") from error


def extract_submission(body):
    if not isinstance(body, str) or len(body.encode("utf-8")) > 100_000:
        raise SubmissionError("投稿正文为空或超过 100 KB。")
    match = re.fullmatch(
        r"\s*### 预设 JSON\s*\n```json\r?\n(.*?)\r?\n```\s*", body, re.DOTALL
    )
    if not match:
        raise SubmissionError("请使用投稿表单，只提交一个完整的预设 JSON；不要自行添加代码围栏或说明。")
    return parse_json(match.group(1))


@dataclass
class Snapshot:
    schema: dict
    index: dict
    paths: set
    base: str = ""
    tree: str = ""
    default_branch: str = "main"

    @classmethod
    def local(cls, root=ROOT):
        return cls(
            parse_json((root / "schema/preset.schema.json").read_text(encoding="utf-8")),
            parse_json((root / "index.json").read_text(encoding="utf-8")),
            {path.relative_to(root).as_posix() for path in root.rglob("*.json")},
        )


def validate_submission(preset, snapshot):
    """Validate uploader JSON; contributor metadata is added only by the Bot."""
    metadata = preset.get("metadata") if isinstance(preset, dict) else None
    if isinstance(metadata, dict) and ({"contributor", "contributor_url"} & set(metadata)):
        raise SubmissionError(
            "投稿 JSON 不得包含 metadata.contributor 或 metadata.contributor_url；Bot 会从 Issue 作者的 GitHub 资料自动填写。"
        )
    Draft202012Validator.check_schema(snapshot.schema)
    errors = []
    try:
        for error in Draft202012Validator(snapshot.schema).iter_errors(preset):
            path = "/" + "/".join(str(part) for part in error.absolute_path)
            errors.append(f"{path}: {error.message}")
    except RecursionError as error:
        raise SubmissionError("JSON 嵌套过深，无法校验。") from error
    if errors:
        raise SubmissionError(errors[:20])
    metadata = preset["metadata"]
    preset_id = metadata["id"]
    if not ID_PATTERN.fullmatch(preset_id) or len(preset_id) > 240:
        errors.append("metadata.id 必须是小写字母、数字及单个连字符分隔，长度不超过 240。")
    if preset_id in RESERVED_IDS:
        errors.append("metadata.id 不能是 example 或系统保留文件名。")
    if preset["$schema"] != "../schema/preset.schema.json":
        errors.append("$schema 必须是 ../schema/preset.schema.json（正式预设路径）。")
    path = f"presets/{preset_id}.json"
    if any(path.casefold() == existing.casefold() for existing in snapshot.paths):
        errors.append(f"正式预设文件已存在，禁止覆盖：{path}")
    for entry in snapshot.index["presets"]:
        if entry["id"] == preset_id or entry["file"].casefold() == path.casefold():
            errors.append(f"index.json 已收录此 ID 或文件：{preset_id}")
            break
    if errors:
        raise SubmissionError(errors)
    return path


def proposed_files(preset, snapshot, contributor):
    path = validate_submission(preset, snapshot)
    stored_preset = copy.deepcopy(preset)
    stored_preset["metadata"].update(contributor)
    validate_stored_submission(stored_preset, snapshot)
    index = {**snapshot.index, "presets": [*snapshot.index["presets"], {
        **{key: preset["metadata"][key] for key in ("id", "name", "description")},
        "file": path,
    }]}
    return {
        path: json.dumps(stored_preset, ensure_ascii=False, indent=2) + "\n",
        "index.json": json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    }


def validate_stored_submission(preset, snapshot):
    """Validate the exact object that will be written to the formal preset tree."""
    for error in Draft202012Validator(snapshot.schema).iter_errors(preset):
        location = "/" + "/".join(str(part) for part in error.absolute_path)
        raise SubmissionError(f"生成的正式预设无效 ({location})：{error.message}")
    return preset


def fingerprint(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def error_block(errors):
    # Keep arbitrary user text inside a code block, without mentions or fences.
    text = json.dumps(errors, ensure_ascii=False, indent=2)
    return "```text\n" + text.replace("`", "\\u0060").replace("@", "\\u0040")[:12000] + "\n```"


class APIError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"GitHub API {status}: {message}")


class GitHub:
    def __init__(self, repository, token, api_url="https://api.github.com"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid GITHUB_REPOSITORY")
        self.repository = repository
        self.base_url = api_url.rstrip("/") + "/repos/" + repository
        self.token = token

    def request(self, method, route, data=None):
        request = Request(self.base_url + route, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }, data=None if data is None else json.dumps(data).encode("utf-8"))
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else None
        except HTTPError as error:
            try:
                message = json.loads(error.read()).get("message", "Request failed")
            except ValueError:
                message = "Request failed"
            raise APIError(error.code, str(message)[:500]) from error

    def optional(self, route):
        try:
            return self.request("GET", route)
        except APIError as error:
            if error.status == 404:
                return None
            raise

    def snapshot(self, default_branch):
        base = self.request("GET", "/git/ref/heads/" + quote(default_branch, safe=""))["object"]["sha"]
        tree = self.request("GET", f"/git/commits/{base}")["tree"]["sha"]
        listing = self.request("GET", f"/git/trees/{tree}?recursive=1")
        if listing.get("truncated"):
            raise RuntimeError("Repository tree was truncated; refusing incomplete duplicate checks")
        blobs = {entry["path"]: entry["sha"] for entry in listing["tree"] if entry["type"] == "blob"}

        def load(path):
            blob = self.request("GET", "/git/blobs/" + blobs[path])
            return parse_json(base64.b64decode(blob["content"]).decode("utf-8"))

        return Snapshot(load("schema/preset.schema.json"), load("index.json"),
                        set(blobs), base, tree, default_branch)

    def ensure_labels(self):
        for name, (color, description) in LABELS.items():
            if self.optional("/labels/" + quote(name, safe="")) is None:
                try:
                    self.request("POST", "/labels", {"name": name, "color": color, "description": description})
                except APIError as error:
                    if error.status != 422:  # A concurrent/manual label creation is harmless.
                        raise

    def contributor_for(self, login):
        user = self.request("GET", "/users/" + quote(login, safe=""))
        contributor = user.get("login")
        profile_url = user.get("html_url")
        if not isinstance(contributor, str) or not CONTRIBUTOR_PATTERN.fullmatch(contributor):
            raise RuntimeError("GitHub returned an invalid contributor username")
        if not isinstance(profile_url, str) or not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_-]*/?", profile_url
        ):
            raise RuntimeError("GitHub returned an invalid contributor profile URL")
        return {
            "contributor": contributor,
            "contributor_url": profile_url.rstrip("/") + "/",
        }


class Bot:
    def __init__(self, api, event):
        self.api = api
        self.event = event
        self.number = int(event["issue"]["number"])
        self.route = f"/issues/{self.number}"
        self.default_branch = event["repository"]["default_branch"]

    def issue(self):
        return self.api.request("GET", self.route)

    @staticmethod
    def labels(issue):
        return {label["name"] for label in issue["labels"]}

    def remove(self, issue, *names):
        for name in names:
            if name in self.labels(issue):
                try:
                    self.api.request("DELETE", self.route + "/labels/" + quote(name, safe=""))
                except APIError as error:
                    if error.status != 404:
                        raise

    def add(self, *names):
        self.api.request("POST", self.route + "/labels", {"labels": list(names)})

    def comment(self, body):
        self.api.request("POST", self.route + "/comments", {"body": body})

    def current(self, body, approved=False):
        issue = self.issue()
        return (issue["state"] == "open" and issue.get("body") == body
                and (not approved or "approved-for-pr" in self.labels(issue)))

    def failure(self, body, errors):
        if not self.current(body):
            return
        issue = self.issue()
        self.remove(issue, "validated", "approved-for-pr", "pr-error")
        self.add("preset-submission", "needs-fix")
        self.comment("预设校验失败。请编辑原 Issue 修正，Bot 会重新检查。\n\n" + error_block(errors))

    def validate(self):
        issue = self.issue()
        if issue["state"] != "open":
            return
        body = issue.get("body")
        self.api.ensure_labels()
        # Revoke approval even when an edited event is processed after another event.
        if self.event["action"] in {"edited", "reopened"}:
            self.remove(issue, "approved-for-pr", "pr-error", "validated", "needs-fix")
            if "pr-created" in self.labels(issue) or "pr-stale" in self.labels(issue):
                self.remove(issue, "pr-created")
                self.add("pr-stale")
        try:
            preset = extract_submission(body)
            path = validate_submission(preset, self.api.snapshot(self.default_branch))
        except SubmissionError as error:
            self.failure(body, error.errors)
            return
        if not self.current(body):
            return
        self.remove(self.issue(), "needs-fix", "pr-error")
        self.add("preset-submission", "validated")
        self.comment(f"预设校验通过：17 个参数字段、元数据、字段类型、ID、文件名及重复检查均通过。\n\n"
                     f"目标文件：`{path}`。空字符串应用时应跳过。\n\n"
                     "等待有仓库写权限的维护者人工审核并添加 `approved-for-pr`。"
                     "若已有 PR，本次编辑不会自动更新该 PR；请核对旧快照。")

    def approved(self, body):
        return self.current(body, approved=True)

    def find_pr(self, branch):
        owner = self.api.repository.split("/")[0]
        query = urlencode({"state": "all", "head": f"{owner}:{branch}", "per_page": 1,
                           "sort": "created", "direction": "desc"})
        pulls = self.api.request("GET", "/pulls?" + query)
        return pulls[0] if pulls else None

    def create_pr(self):
        if self.event["action"] != "labeled" or self.event.get("label", {}).get("name") != "approved-for-pr":
            return
        self.api.ensure_labels()
        issue = self.issue()
        body = issue.get("body")
        sender = quote(self.event["sender"]["login"], safe="")
        permission = self.api.request("GET", f"/collaborators/{sender}/permission")
        if permission["permission"] not in {"write", "maintain", "admin"}:
            self.remove(issue, "approved-for-pr")
            self.comment("未创建 PR：只有具备仓库 write、maintain 或 admin 权限的维护者才能批准投稿。")
            return
        if not self.approved(body):
            return
        if body != self.event["issue"].get("body"):
            self.remove(issue, "approved-for-pr")
            self.comment("未创建 PR：审批事件对应的正文已过期。请等待重新校验后重新添加审批标签。")
            return
        try:
            preset = extract_submission(body)
            snapshot = self.api.snapshot(self.default_branch)
            contributor = self.api.contributor_for(issue["user"]["login"])
            files = proposed_files(preset, snapshot, contributor)
            preset_id = preset["metadata"]["id"]
            branch = f"preset-submissions/{preset_id}"
            marker = f"<!-- preset-submission:{self.number}:{fingerprint(body)} -->"
            existing = self.find_pr(branch)
            if existing:
                if marker not in (existing.get("body") or ""):
                    raise SubmissionError("此 ID 已有投稿 PR，或原 Issue 在生成 PR 后已编辑。禁止重复创建或覆盖；请人工核对旧 PR。")
                if self.approved(body):
                    self.finish(body, existing["html_url"])
                return
            ref = "/git/ref/heads/" + quote(branch, safe="")
            if self.api.optional(ref):
                raise SubmissionError("此 ID 的投稿分支已存在但没有对应 PR，禁止覆盖。请维护者检查后删除遗留分支，再重新审批。")
            # Check the current body/approval again immediately before any Git write.
            if not self.approved(body):
                return
            entries = []
            for path, content in files.items():
                blob = self.api.request("POST", "/git/blobs", {"content": content, "encoding": "utf-8"})
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            tree = self.api.request("POST", "/git/trees", {"base_tree": snapshot.tree, "tree": entries})
            commit = self.api.request("POST", "/git/commits", {
                "message": f"Add preset {preset_id} from issue #{self.number}",
                "tree": tree["sha"], "parents": [snapshot.base],
            })
            if not self.approved(body):
                return
            head = self.api.request("GET", "/git/ref/heads/" + quote(self.default_branch, safe=""))
            if head["object"]["sha"] != snapshot.base:
                raise SubmissionError("默认分支在校验后已更新，未创建投稿分支。请重新添加审批标签以重新检查重复项。")
            self.api.request("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
            if not self.approved(body):
                self.comment("Issue 在分支生成期间发生变化，未创建 PR。请人工检查遗留投稿分支后重新审批。")
                return
            pull = self.api.request("POST", "/pulls", {
                "title": f"Add device preset: {preset_id}",
                "head": branch, "base": snapshot.default_branch,
                "body": f"新增正式预设 `{preset_id}` 及其 index.json 条目。\n\n"
                        f"来源：#{self.number}\n\n{marker}\n\n"
                        "已按审批时的 Issue 正文重新校验；请人工核对参数来源和空字符串跳过语义。"
                        "本 PR 仅等待人工审核与合并，不会自动合并。",
            })
            self.finish(body, pull["html_url"])
        except SubmissionError as error:
            self.failure(body, error.errors)

    def finish(self, body, url):
        issue = self.issue()
        self.remove(issue, "approved-for-pr", "needs-fix", "pr-error")
        if issue.get("body") == body and issue["state"] == "open":
            self.remove(issue, "pr-stale")
            self.add("validated", "pr-created")
            self.comment(f"PR 已生成或已存在：{url}\n\n等待维护者人工审核和合并；Bot 不会自动合并。")
        else:
            self.remove(issue, "validated", "pr-created")
            self.add("pr-stale")
            self.comment(f"PR 已创建，但 Issue 随后发生变化：{url}\n\nPR 保留旧快照，请人工核对，不能视为最新正文的审批结果。")

    def operation_failed(self, mode, error):
        issue = self.issue()
        self.remove(issue, "approved-for-pr", "validated")
        if mode == "create-pr":
            self.add("pr-error")
        self.comment("Bot 操作失败，不能视为校验或创建 PR 成功。请维护者检查 Actions 日志和仓库权限。\n\n"
                     + error_block([str(error)]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "create-pr", "check-file"))
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()
    if args.mode == "check-file":
        if not args.file:
            parser.error("check-file requires a JSON file")
        try:
            preset = parse_json(Path(args.file).read_text(encoding="utf-8"))
            print("OK:", validate_submission(preset, Snapshot.local()))
        except SubmissionError as error:
            raise SystemExit(str(error)) from error
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"],
                 os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    bot = Bot(api, event)
    try:
        if args.mode == "validate":
            bot.validate()
        else:
            bot.create_pr()
    except Exception as error:
        bot.operation_failed(args.mode, error)
        raise


if __name__ == "__main__":
    main()
