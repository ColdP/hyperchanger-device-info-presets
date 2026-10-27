# HyperChanger 手机参数预设库

本仓库提供 HyperChanger 的手机参数云端预设，仅保存静态 JSON，不需要数据库或后端。参数用于设备信息的显示覆盖，不修改系统属性。

## 文件与格式

- `presets/<id>.json`：正式可应用的设备预设，目前包含 `xiaomi-18-pro.json` 和 `xiaomi-17-pro-fix.json`。
- [`example.json`](example.json)：含全部 17 个参数字段的空白投稿模板；贡献者信息由 Bot 生成，不应填写在投稿 JSON 中。
- [`index.json`](index.json)：正式预设列表，含格式版本及每个预设的 `id`、`name`、`description`、`file`。
- [`schema/preset.schema.json`](schema/preset.schema.json)：JSON Schema Draft 2020-12，检查预设结构及字段类型。
- [`scripts/validate.py`](scripts/validate.py)：本地校验入口。

预设顶层包含 `$schema`、`metadata`、`parameters`：

| 位置 | 内容 |
| --- | --- |
| `$schema` | 本文件使用的 Schema 路径；正式预设为 `../schema/preset.schema.json`，根目录模板为 `schema/preset.schema.json` |
| `metadata.id` | 稳定且唯一的 ID，使用小写字母、数字及单个连字符分隔，例如 `xiaomi-18-pro`；对应文件名，不含扩展名 |
| `metadata.name` | 预设显示名称 |
| `metadata.description` | 预设说明，与手机参数分开存储 |
| `metadata.schema_version` | 字符串 `"1.0"`，表示预设格式版本 |
| `metadata.contributor` | 正式预设中的 GitHub 投稿者用户名，由 Bot 从 Issue 作者资料写入 |
| `metadata.contributor_url` | 正式预设中的 GitHub 主页链接，由 Bot 从 Issue 作者资料写入 |
| `parameters` | 下表中全部 17 个存储 Key，值一律为字符串 |

**空字符串 `""` 表示该参数未提供。HyperChanger 应跳过该字段，保留用户已有设置，绝不能把空字符串直接写入设置以清空原值。** 非空字符串才参与应用；不使用 `null`、数字或省略字段表示未知参数。该规则由后续客户端导入逻辑执行，Schema 只能检查结构与类型。

`model` 与 `device_detail_model`、`processor` 与 `device_detail_processor` 分别独立。不能用一个字段的值自动填补另一个字段。Xiaomi 18 Pro 参考预设仅采用已提供的 9 项数据，其余 8 项为空；前后摄分别填写，未提供的相机总览 `camera` 留空。

`metadata.contributor` 和 `metadata.contributor_url` 只存在于正式 `presets/` 文件中。投稿 JSON（包括 `example.json`）必须省略这两个字段；Bot 根据 Issue 作者的 GitHub 用户资料自动获取并写入，投稿者提供这两个字段会被拒绝。当前两个正式预设的贡献者均为 `btm_m`，主页为 `https://github.com/ColdP/`。

## 参数字段对应关系

JSON 仅使用第一列的存储 Key；Kotlin 字段名只用于说明对应关系，不写入 JSON。

| 存储 Key | Kotlin 字段 | 含义 |
| --- | --- | --- |
| `model` | `model` | 手机型号 |
| `processor` | `processor` | 设备卡片中的处理器 |
| `ram` | `ram` | 运行内存 |
| `battery` | `battery` | 电池容量 |
| `screen_size` | `screenSize` | 屏幕尺寸 |
| `resolution` | `resolution` | 分辨率 |
| `device_detail_model` | `detailModel` | 全部参数中的认证型号 |
| `device_detail_processor` | `detailProcessor` | 全部参数中的处理器 |
| `camera` | `camera` | 相机总览 |
| `os_version` | `osVersion` | 系统版本 |
| `android_version` | `androidVersion` | Android 版本 |
| `storage` | `storage` | 存储容量 |
| `kernel` | `kernel` | 内核版本 |
| `baseband` | `baseband` | 基带版本 |
| `hardware` | `hardware` | 硬件信息 |
| `camera_rear` | `cameraRear` | 后摄参数 |
| `camera_front` | `cameraFront` | 前摄参数 |

## 新增预设

1. 复制 `example.json` 到 `presets/<id>.json`，将 `$schema` 改为 `../schema/preset.schema.json`。
2. 填写元数据和已确认的参数，保留全部 17 个字段；未知参数使用空字符串，不猜测、不补造。不要添加 `metadata.contributor` 或 `metadata.contributor_url`。
3. 在 `index.json` 的 `presets` 数组中加入 `id`、`name`、`description` 和 `file`（如 `presets/xiaomi-18-pro.json`）。前三项与预设元数据一致，`file` 是相对 `index.json` 的路径。不要收录 `example.json`。
4. 运行本地校验后提交。

## 验证 JSON

运行时只有静态 JSON；本地校验使用 Python 3 和标准 JSON Schema 校验库 `jsonschema`，没有额外运行时依赖。在仓库根目录执行：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/validate.py
```

脚本解析仓库中全部 JSON（跳过隐藏目录，例如 `.venv` 和 `.git`），拒绝重复 Key 与非标准数值常量，检查 Schema 本身，并校验 `example.json` 和所有正式预设。它还检查索引字段、唯一 ID、文件名、路径、元数据及正式预设收录完整性。预设 Schema 要求全部 17 个参数为字符串，并拒绝未知字段。

## 后续客户端接入

从 `index.json` 获取正式列表，按相对路径读取预设，并在应用前检查格式版本及结构。只对 `parameters` 中的非空值进行合并更新；元数据不写入设备设置，也不自动修改启用状态或处理器图标。对于不支持的格式版本应拒绝应用，避免错误覆盖已有配置。需要改变现有字段语义或结构时，先协调客户端支持并升级格式版本。

## 通过 GitHub Issue 投稿

登录 GitHub，打开 **Issues → New issue → 手机参数预设投稿**。根据 `example.json` 填写完整 JSON，修改 `metadata.id`、名称和说明，将 `$schema` 改为 `../schema/preset.schema.json`。在表单里粘贴纯 JSON，不要再套 Markdown 代码围栏。未知参数保留空字符串，不能用示例值冒充真实参数。

文件名不单独提交，由 ID 派生为 `presets/{id}.json`。投稿入口额外限制 ID 不超过 240 个字符，拒绝 `example` 以及 Windows 保留文件名，避免非法路径。现有预设格式、Schema 和索引结构均保持不变。

Issue 创建、编辑或重新打开后，Bot 都会读取**当前正文**并使用默认分支上的最新 Schema 和索引检查 JSON、元数据、17 个参数、未知字段、文件名和重复项。失败会回复具体原因并标记 `needs-fix`；成功会回复结果并标记 `validated`。两种结果互斥，`validated` 仅表示格式合格，不代表参数事实已获认可。

维护者人工核实数据后，手动添加 `approved-for-pr`。Bot 会通过 GitHub API 检查**添加此标签者**是否具备仓库 `write`、`maintain` 或 `admin` 权限，读取 Issue 作者的 GitHub 用户名和主页链接，比较审批事件与当前正文，重新校验后才创建分支和 PR。投稿者或只有 `triage` 权限的用户不能通过加标签获得创建权限。

生成的分支为 `preset-submissions/{id}`。PR 只添加正式预设并追加索引；正式预设的 `metadata.contributor` 和 `metadata.contributor_url` 来自 Issue 作者的 GitHub API 资料，不来自投稿 JSON。Bot 不修改模板、Schema 或已有预设。原 Issue 收到 PR 链接及 `pr-created` 标签，审批标签随即移除。PR 等待人工审核与合并，Bot 没有自动合并逻辑。

### 编辑、重复投稿与失败恢复

- 编辑或重新打开 Issue 会撤销 `approved-for-pr` 并重新校验。审批结果不跨编辑复用。
- 若已生成 PR，编辑 Issue 会标记 `pr-stale`，原 PR 保留原快照，不自动更新。请人工核对 PR 与当前正文；同一稳定 ID 的修正应由维护者直接修改或重新打开旧 PR，并运行校验。已有 PR（包括已关闭 PR）不会被覆盖或重复生成。只有提交另一份不冲突 ID 的预设时，才应新建投稿；不要为了绕过重复检查随意修改同一设备的稳定 ID。
- 同一审批重复执行时，会复用相同 Issue、正文指纹及 ID 的已有 PR。不同 Issue 或不同正文不能复用同一 ID 的 PR。相同 ID 的并发请求由 GitHub 创建分支的唯一性约束兜底，绝不强制更新分支。
- 若默认分支在校验后变动，Bot 会停止创建并提示重新审批。不同 ID 的 PR 可能基于同一版索引生成；如发生合并冲突，请维护者在合并前保留双方索引条目并运行校验。
- GitHub API 或权限失败会回复原因，创建失败时标记 `pr-error`。请修复配置后重新添加审批标签，或在 Actions 中重跑。若已留下分支但没有 PR，Bot 会拒绝覆盖；请人工检查该分支，手动创建 PR，或删除遗留分支后重新审批。

### GitHub 网页上需要配置

1. 将本仓库文件提交到默认分支（目前为 `main`）。Issue 工作流必须已存在于默认分支上；本地文件不会自动启用云端 Bot。
2. 在 **Settings → General → Features** 启用 **Issues**；在 **Settings → Actions → General** 启用 Actions，允许 `actions/checkout` 和 `actions/setup-python` 运行。
3. 在 **Settings → Actions → General → Workflow permissions** 勾选 **Allow GitHub Actions to create and approve pull requests** 并保存。这里允许创建 PR，并不代表 Bot 会审批或合并 PR。工作流已通过各自的 `permissions` 声明需要的写权限；默认权限可继续使用只读，无需把所有工作流的默认权限设为读写。若组织策略禁用写权限或创建 PR，需要组织管理员放行。
4. 首次开放投稿前，在 **Issues → Labels** 创建 `preset-submission` 标签，以便表单自动打标。Bot 首次运行会补齐其余状态标签（`needs-fix`、`validated`、`approved-for-pr`、`pr-created`、`pr-stale`、`pr-error`），也可事先手动创建。
5. 维护者应有仓库写权限。建议给默认分支配置人工审核的分支保护，且不要为投稿 PR 配置其他自动合并 Bot。

只使用 Actions 自动提供的 `GITHUB_TOKEN`，无需个人访问令牌、仓库 Secret 或匿名上传 API。校验工作流仅需 `contents: read`、`issues: write`；创建 PR 工作流才使用 `contents: write`、`issues: write`、`pull-requests: write`。两个工作流都从默认分支检出可信脚本，不使用 `pull_request_target`，不检出投稿分支执行代码，不将 Issue 文本插入 shell。

注意：由 `GITHUB_TOKEN` 创建的 PR 通常不会再触发常规 `pull_request` 工作流。不要因此把缺少 CI 结果当作通过；本 Bot 在创建前已重新校验，合并前仍需维护者人工审核，必要时本地运行校验。

### 测试一条投稿

1. 登录 GitHub，使用投稿表单提交一个新 ID（如 `submission-test`）的完整预设。可用空参数测试格式，但不要将测试投稿合并为真实设备数据。确认得到 `validated` 及校验回复。
2. 编辑 JSON，把 `ram` 改成数字或删掉一个字段，确认变为 `needs-fix`，回复中包含具体字段错误；修正为字符串后确认恢复 `validated`。
3. 由有写权限的维护者添加 `approved-for-pr`，确认产生仅修改预设和索引的 PR、Issue 收到链接、出现 `pr-created`，且没有自动合并。
4. 再次添加审批标签，确认复用已有 PR；编辑 Issue，确认撤销审批并出现 `pr-stale`，旧 PR 内容没有被自动替换。
5. 检查结束后关闭测试 PR/Issue 并删除测试分支，不要合并测试预设。

本地测试（无需 GitHub Token，不会向 GitHub 发请求）：

```powershell
python -m pip install -r requirements-dev.txt
python scripts/validate.py
python -m unittest discover -s tests -v
python scripts/submission_bot.py check-file path/to/new-preset.json
```

`check-file` 检查新投稿，故检查已收录的参考预设会报告重复；校验已收录文件请使用 `scripts/validate.py`。测试覆盖字段类型、重复 Key、非法 ID、重复文件、审批权限、编辑失效、幂等、竞争及 API 失败等情况。工作流 YAML 可使用外部工具 `actionlint .github/workflows/validate-submission.yml .github/workflows/create-preset-pr.yml` 检查，不需要把它作为项目依赖。

本仓库仍不包含 HyperChanger 客户端导入逻辑、数据库或后端。
