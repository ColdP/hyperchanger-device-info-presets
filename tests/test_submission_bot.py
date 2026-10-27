import copy
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from submission_bot import (  # noqa: E402
    APIError, Bot, GitHub, Snapshot, SubmissionError, extract_submission,
    parse_json, proposed_files, validate_submission,
)


def candidate():
    preset = json.loads((ROOT / "example.json").read_text(encoding="utf-8"))
    preset["$schema"] = "../schema/preset.schema.json"
    preset["metadata"]["id"] = "submission-test"
    return preset


def issue_body(preset=None):
    return "### 预设 JSON\n\n```json\n" + json.dumps(
        candidate() if preset is None else preset, ensure_ascii=False, indent=2
    ) + "\n```"


class FakeGitHub:
    repository = "ColdP/hyperchanger-device-info-presets"

    def __init__(self, body=None, labels=(), permission="write"):
        self.issue = {"number": 7, "state": "open", "body": body or issue_body(),
                      "labels": [{"name": label} for label in labels],
                      "user": {"login": "maintainer"}}
        self.permission = permission
        self.data = Snapshot.local()
        self.data.base, self.data.tree = "base-sha", "base-tree"
        self.head = "base-sha"
        self.refs, self.pulls, self.calls, self.comments = {}, [], [], []
        self.on_write = None

    def ensure_labels(self):
        pass

    def snapshot(self, default_branch):
        return copy.deepcopy(self.data)

    def contributor_for(self, login):
        return {"contributor": login, "contributor_url": f"https://github.com/{login}/"}

    def optional(self, route):
        if route.startswith("/git/ref/heads/"):
            return self.refs.get(unquote(route.removeprefix("/git/ref/heads/")))
        raise AssertionError(route)

    def request(self, method, route, data=None):
        self.calls.append((method, route, copy.deepcopy(data)))
        if method == "GET" and route == "/issues/7":
            return copy.deepcopy(self.issue)
        if method == "GET" and route.startswith("/collaborators/"):
            return {"permission": self.permission}
        if method == "GET" and route == "/users/maintainer":
            return {"login": "maintainer", "html_url": "https://github.com/maintainer"}
        if method == "DELETE" and route.startswith("/issues/7/labels/"):
            label = unquote(route.rsplit("/", 1)[1])
            self.issue["labels"] = [item for item in self.issue["labels"] if item["name"] != label]
            return None
        if method == "POST" and route == "/issues/7/labels":
            names = {item["name"] for item in self.issue["labels"]} | set(data["labels"])
            self.issue["labels"] = [{"name": name} for name in sorted(names)]
            return self.issue["labels"]
        if method == "POST" and route == "/issues/7/comments":
            self.comments.append(data["body"])
            return {}
        if method == "GET" and route.startswith("/pulls?"):
            head = parse_qs(urlsplit(route).query)["head"][0].split(":", 1)[1]
            return [pull for pull in self.pulls if pull["head"] == head]
        if method == "GET" and route.startswith("/git/ref/heads/"):
            return {"object": {"sha": self.head}}
        if method == "POST" and route in {"/git/blobs", "/git/trees", "/git/commits"}:
            if self.on_write:
                self.on_write(route)
            return {"sha": "generated-sha"}
        if method == "POST" and route == "/git/refs":
            branch = data["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                raise APIError(422, "Reference already exists")
            self.refs[branch] = {"object": {"sha": data["sha"]}}
            return self.refs[branch]
        if method == "POST" and route == "/pulls":
            pull = {**data, "html_url": "https://github.com/ColdP/hyperchanger-device-info-presets/pull/8"}
            self.pulls.append(pull)
            return pull
        raise AssertionError((method, route, data))

    def labels(self):
        return {item["name"] for item in self.issue["labels"]}

    def git_writes(self):
        return [call for call in self.calls if call[0] != "GET" and call[1].startswith("/git/")]


def bot(api, action="labeled"):
    return Bot(api, {"action": action, "issue": copy.deepcopy(api.issue),
                     "label": {"name": "approved-for-pr"}, "sender": {"login": "maintainer"},
                     "repository": {"default_branch": "main"}})


class SubmissionValidationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = Snapshot.local()

    def test_valid_form_and_crlf(self):
        for body in (issue_body(), issue_body().replace("\n", "\r\n")):
            self.assertEqual(validate_submission(extract_submission(body), self.snapshot),
                             "presets/submission-test.json")

    def test_strict_json_and_form(self):
        for body in ("", "{}", issue_body() + "\n```sh\necho unsafe\n```", "x" * 100001):
            with self.subTest(body=body[:20]), self.assertRaises(SubmissionError):
                extract_submission(body)
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{bad}'):
            with self.subTest(text=text), self.assertRaises(SubmissionError):
                parse_json(text)

    def test_recursion_errors_are_reported_as_submission_errors(self):
        with patch("submission_bot.json.loads", side_effect=RecursionError("nested too deeply")):
            with self.assertRaises(SubmissionError):
                parse_json("{}")
        with (
            patch("submission_bot.Draft202012Validator.check_schema"),
            patch("submission_bot.Draft202012Validator.iter_errors", side_effect=RecursionError("nested too deeply")),
        ):
            with self.assertRaises(SubmissionError):
                validate_submission(candidate(), self.snapshot)

    def test_every_parameter_required_and_string(self):
        self.assertEqual(len(candidate()["parameters"]), 17)
        for key in candidate()["parameters"]:
            for value in (None, 123, False, [], {}):
                preset = candidate()
                preset["parameters"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(SubmissionError):
                    validate_submission(preset, self.snapshot)
            preset = candidate()
            del preset["parameters"][key]
            with self.subTest(missing=key), self.assertRaises(SubmissionError):
                validate_submission(preset, self.snapshot)

    def test_metadata_and_unknown_fields(self):
        for key in candidate()["metadata"]:
            preset = candidate()
            del preset["metadata"][key]
            with self.subTest(missing=key), self.assertRaises(SubmissionError):
                validate_submission(preset, self.snapshot)
            preset = candidate()
            preset["metadata"][key] = 123
            with self.subTest(wrong_type=key), self.assertRaises(SubmissionError):
                validate_submission(preset, self.snapshot)
        for group in (None, "metadata", "parameters"):
            preset = candidate()
            (preset if group is None else preset[group])["unsupported"] = ""
            with self.subTest(group=group), self.assertRaises(SubmissionError):
                validate_submission(preset, self.snapshot)
        preset = candidate()
        preset["metadata"]["schema_version"] = "2.0"
        with self.assertRaises(SubmissionError):
            validate_submission(preset, self.snapshot)

    def test_contributor_fields_are_bot_only(self):
        for key, value in (("contributor", "someone"), ("contributor_url", "https://github.com/someone/")):
            preset = candidate()
            preset["metadata"][key] = value
            with self.subTest(key=key), self.assertRaises(SubmissionError):
                validate_submission(preset, self.snapshot)

    def test_unsafe_reserved_duplicate_ids_and_schema_path(self):
        for preset_id in ("../escape", "UPPER", "a/b", "a--b", "a\n", "example", "con", "lpt1", "a" * 241, "xiaomi-18-pro"):
            preset = candidate()
            preset["metadata"]["id"] = preset_id
            with self.subTest(preset_id=preset_id), self.assertRaises(SubmissionError):
                validate_submission(preset, self.snapshot)
        preset = candidate()
        preset["$schema"] = "schema/preset.schema.json"
        with self.assertRaises(SubmissionError):
            validate_submission(preset, self.snapshot)

    def test_index_and_unindexed_file_collisions(self):
        for source in ("index", "file"):
            snapshot = copy.deepcopy(self.snapshot)
            if source == "index":
                snapshot.index["presets"].append({"id": "submission-test", "file": "presets/submission-test.json"})
            else:
                snapshot.paths.add("presets/SUBMISSION-TEST.json")
            with self.subTest(source=source), self.assertRaises(SubmissionError):
                validate_submission(candidate(), snapshot)

    def test_generation_preserves_format_and_treats_commands_as_data(self):
        preset = candidate()
        preset["parameters"]["hardware"] = '$(touch /tmp/unsafe); `whoami` ${TOKEN}'
        before = copy.deepcopy(self.snapshot.index)
        files = proposed_files(
            preset,
            self.snapshot,
            {"contributor": "maintainer", "contributor_url": "https://github.com/maintainer/"},
        )
        self.assertEqual(set(files), {"presets/submission-test.json", "index.json"})
        stored = json.loads(files["presets/submission-test.json"])
        self.assertEqual(stored["metadata"]["contributor"], "maintainer")
        self.assertEqual(stored["metadata"]["contributor_url"], "https://github.com/maintainer/")
        stored["metadata"].pop("contributor")
        stored["metadata"].pop("contributor_url")
        self.assertEqual(stored, preset)
        index = json.loads(files["index.json"])
        self.assertEqual(index["presets"][:-1], before["presets"])
        self.assertEqual(index["schema_version"], before["schema_version"])
        self.assertEqual(self.snapshot.index, before)
        self.assertNotIn("example.json", [entry["file"] for entry in index["presets"]])


class BotFlowTests(unittest.TestCase):
    def test_old_event_uses_latest_body_not_event_payload(self):
        api = FakeGitHub(labels=["validated"])
        instance = bot(api, "opened")
        api.issue["body"] = "invalid latest content"
        instance.validate()
        self.assertIn("needs-fix", api.labels())
        self.assertNotIn("validated", api.labels())

    def test_validation_then_edit_failure_then_correction(self):
        api = FakeGitHub()
        bot(api, "opened").validate()
        self.assertIn("validated", api.labels())
        api.issue["labels"].extend({"name": name} for name in ("approved-for-pr", "pr-created"))
        api.issue["body"] = "bad JSON"
        bot(api, "edited").validate()
        self.assertTrue({"needs-fix", "pr-stale"} <= api.labels())
        self.assertFalse({"validated", "approved-for-pr", "pr-created"} & api.labels())
        self.assertIn("投稿表单", api.comments[-1])
        api.issue["body"] = issue_body()
        bot(api, "edited").validate()
        self.assertIn("validated", api.labels())
        self.assertNotIn("needs-fix", api.labels())
        self.assertFalse(api.git_writes())

    def test_only_write_maintainers_can_create(self):
        for permission in ("read", "triage", "none"):
            api = FakeGitHub(labels=["approved-for-pr"], permission=permission)
            bot(api).create_pr()
            self.assertFalse(api.git_writes())
            self.assertFalse(api.pulls)
            self.assertNotIn("approved-for-pr", api.labels())

    def test_wrong_event_no_approval_closed_or_stale_event(self):
        for scenario in ("wrong-label", "wrong-action", "no-approval", "closed", "changed"):
            api = FakeGitHub(labels=["approved-for-pr"])
            instance = bot(api)
            if scenario == "wrong-label":
                instance.event["label"]["name"] = "validated"
            elif scenario == "wrong-action":
                instance.event["action"] = "edited"
            elif scenario == "no-approval":
                api.issue["labels"] = []
            elif scenario == "closed":
                api.issue["state"] = "closed"
            else:
                api.issue["body"] += "\n"
            instance.create_pr()
            with self.subTest(scenario=scenario):
                self.assertFalse(api.git_writes())
                self.assertFalse(api.pulls)

    def test_create_and_repeat_are_idempotent_and_never_merge(self):
        api = FakeGitHub(labels=["approved-for-pr"])
        bot(api).create_pr()
        self.assertEqual(len(api.pulls), 1)
        self.assertEqual(set(api.refs), {"preset-submissions/submission-test"})
        self.assertIn("pr-created", api.labels())
        self.assertNotIn("approved-for-pr", api.labels())
        tree = next(data for method, route, data in api.calls if route == "/git/trees")
        self.assertEqual({entry["path"] for entry in tree["tree"]}, {"presets/submission-test.json", "index.json"})
        count = len(api.git_writes())
        api.issue["labels"].append({"name": "approved-for-pr"})
        bot(api).create_pr()
        self.assertEqual(len(api.pulls), 1)
        self.assertEqual(len(api.git_writes()), count)
        self.assertFalse(any("merge" in route or method == "PATCH" for method, route, data in api.calls))

    def test_revalidate_before_create(self):
        preset = candidate()
        preset["parameters"]["ram"] = 123
        api = FakeGitHub(body=issue_body(preset), labels=["validated", "approved-for-pr"])
        bot(api).create_pr()
        self.assertIn("needs-fix", api.labels())
        self.assertNotIn("validated", api.labels())
        self.assertFalse(api.git_writes())

    def test_existing_branch_or_other_submission_pr_is_not_overwritten(self):
        for scenario in ("orphan-branch", "other-pr"):
            api = FakeGitHub(labels=["approved-for-pr"])
            if scenario == "orphan-branch":
                api.refs["preset-submissions/submission-test"] = {"object": {"sha": "old"}}
            else:
                api.pulls.append({"head": "preset-submissions/submission-test", "body": "other issue"})
            bot(api).create_pr()
            with self.subTest(scenario=scenario):
                self.assertFalse(api.git_writes())
                self.assertIn("needs-fix", api.labels())

    def test_edit_during_git_operations_or_base_change_never_creates_pr(self):
        for scenario in ("edited", "approval-removed", "base-changed"):
            api = FakeGitHub(labels=["approved-for-pr"])

            def change(route):
                if scenario == "edited":
                    api.issue["body"] += "\n"
                elif scenario == "approval-removed":
                    api.issue["labels"] = []
                else:
                    api.head = "new-base"

            api.on_write = change
            bot(api).create_pr()
            with self.subTest(scenario=scenario):
                self.assertFalse(api.refs)
                self.assertFalse(api.pulls)

    def test_edit_after_pr_creation_marks_stale(self):
        api = FakeGitHub(labels=["approved-for-pr", "validated"])
        instance = bot(api)
        original_finish = instance.finish

        def edit_then_finish(body, url):
            api.issue["body"] += "\n"
            original_finish(body, url)

        instance.finish = edit_then_finish
        instance.create_pr()
        self.assertEqual(len(api.pulls), 1)
        self.assertIn("pr-stale", api.labels())
        self.assertFalse({"pr-created", "validated", "approved-for-pr"} & api.labels())

    def test_concurrent_same_id_reference_creation_cannot_overwrite(self):
        api = FakeGitHub(labels=["approved-for-pr"])

        def competing_branch(route):
            if route == "/git/commits":
                api.refs["preset-submissions/submission-test"] = {"object": {"sha": "other-submission"}}

        api.on_write = competing_branch
        with self.assertRaises(APIError) as error:
            bot(api).create_pr()
        self.assertEqual(error.exception.status, 422)
        self.assertEqual(api.refs["preset-submissions/submission-test"]["object"]["sha"], "other-submission")
        self.assertFalse(api.pulls)

    def test_api_failure_is_explicit_and_revokes_success(self):
        api = FakeGitHub(labels=["approved-for-pr", "validated"])
        bot(api).operation_failed("create-pr", APIError(403, "Actions cannot create PRs"))
        self.assertIn("pr-error", api.labels())
        self.assertNotIn("validated", api.labels())
        self.assertNotIn("approved-for-pr", api.labels())
        self.assertIn("403", api.comments[-1])


class SnapshotTests(unittest.TestCase):
    def test_contributor_profile_is_read_from_github_and_normalized(self):
        class UserAPI(GitHub):
            def request(self, method, route, data=None):
                assert method == "GET"
                assert route == "/users/ColdP"
                return {"login": "ColdP", "html_url": "https://github.com/ColdP"}

        api = UserAPI("ColdP/hyperchanger-device-info-presets", "test-token")
        self.assertEqual(api.contributor_for("ColdP"), {
            "contributor": "ColdP",
            "contributor_url": "https://github.com/ColdP/",
        })

    def test_invalid_contributor_profile_response_is_rejected(self):
        class UserAPI(GitHub):
            def request(self, method, route, data=None):
                return {"login": "bad/user", "html_url": "https://example.com/bad/user"}

        with self.assertRaisesRegex(RuntimeError, "invalid contributor username"):
            UserAPI("ColdP/hyperchanger-device-info-presets", "test-token").contributor_for("ColdP")

    def test_remote_snapshot_uses_current_default_branch_and_blobs(self):
        local = Snapshot.local()

        class ReadAPI(GitHub):
            def request(self, method, route, data=None):
                self.assert_read(method)
                if route == "/git/ref/heads/main":
                    return {"object": {"sha": "latest-base"}}
                if route == "/git/commits/latest-base":
                    return {"tree": {"sha": "latest-tree"}}
                if route == "/git/trees/latest-tree?recursive=1":
                    return {"truncated": False, "tree": [
                        {"path": "schema/preset.schema.json", "type": "blob", "sha": "schema"},
                        {"path": "index.json", "type": "blob", "sha": "index"},
                        {"path": "presets/xiaomi-18-pro.json", "type": "blob", "sha": "preset"},
                    ]}
                payload = {"/git/blobs/schema": local.schema, "/git/blobs/index": local.index}[route]
                return {"content": base64.b64encode(json.dumps(payload).encode()).decode()}

            @staticmethod
            def assert_read(method):
                if method != "GET":
                    raise AssertionError(method)

        snapshot = ReadAPI("ColdP/hyperchanger-device-info-presets", "test-token").snapshot("main")
        self.assertEqual(snapshot.base, "latest-base")
        self.assertEqual(snapshot.tree, "latest-tree")
        self.assertEqual(snapshot.index, local.index)
        with self.assertRaises(SubmissionError):
            existing = candidate()
            existing["metadata"]["id"] = "xiaomi-18-pro"
            validate_submission(existing, snapshot)

    def test_truncated_remote_tree_refuses_duplicate_check(self):
        class TruncatedAPI(GitHub):
            def request(self, method, route, data=None):
                if route.startswith("/git/ref/"):
                    return {"object": {"sha": "base"}}
                if route.startswith("/git/commits/"):
                    return {"tree": {"sha": "tree"}}
                return {"truncated": True, "tree": []}

        with self.assertRaisesRegex(RuntimeError, "truncated"):
            TruncatedAPI("ColdP/hyperchanger-device-info-presets", "test-token").snapshot("main")


if __name__ == "__main__":
    unittest.main()
