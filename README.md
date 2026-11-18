# 汽车技术日报

独立 Python 3.12 项目：免费 RSS、关键词筛选、原文摘录、QQ 邮件和飞书群机器人。没有付费 AI、没有 NAS、没有外部发信 Action。运行于公开 GitHub 仓库的 `ubuntu-latest` 标准 runner，应用只使用 Python 标准库，Linux 自带 IANA 时区数据库。

## 发布与运行

仓库为 `WweiYweiYW/auto-tech-daily`，项目文件放在仓库根目录，默认分支为 `main`。本次双渠道改造仅在本地实现和 mock 验证，发布由仓库所有者负责。

```sh
python -m unittest discover -s tests -v
python daily.py --dry-run
python daily.py
```

Windows 如果缺少系统 IANA 时区数据库，可在自己的虚拟环境安装 `tzdata`；生产 Ubuntu 无需 pip 安装。`--dry-run` 会实际拉取源，输出文章预览，不输出邮箱或机器人凭据配置，既不写状态也不向任何渠道投递。不要将预览重定向后提交仓库。

邮件、飞书独立启用，任一渠道配置完整即可工作。所有渠道未配置时只采集并提示“未配置”，不承诺已经发送；只配置部分必需项时提示“配置不完整”，不使任务失败。已启用渠道发送失败则任务返回非零，但仍尝试另一渠道。两个源全失败返回非零并暂停本次所有投递；一个源失败仍处理其余源。每源 20 秒超时，最多尝试 3 次，读取上限 5 MiB。关键词在 `config.json` 修改：IT之家必须同时具有汽车语境，Ars Cars 使用专门汽车源语境；优惠促销排除优先。该规则可能漏报或误报，不保证完整覆盖。

## QQ 邮件配置

登录 QQ 邮箱，依官方帮助 <https://service.mail.qq.com/> 开启 SMTP 服务并获取授权码。使用 `SMTP_SSL` 连接 `smtp.qq.com:465`。这里填写授权码，而不是 QQ 登录密码；不要在聊天、issue、日志或代码中分享授权码。

在 [仓库 Actions secrets 设置](https://github.com/WweiYweiYW/auto-tech-daily/settings/secrets/actions) 中添加：

- `SMTP_USERNAME`：发件 QQ 邮箱。
- `SMTP_PASSWORD`：QQ 邮箱 SMTP 授权码。
- `MAIL_TO`：可选，收件邮箱；省略则发给发件邮箱自身。建议只填写一个有效地址。

两项 SMTP 凭据齐全后，发送错误会使任务失败，并保留待发送记录；日志不打印邮箱配置、授权码或 SMTP 异常原文。不要把 `.env`、凭据文件或邮箱地址写入代码。程序不自动读取 `.env`，本地配置需设置环境变量。

## 飞书群机器人配置

在飞书目标群添加自定义机器人，将 webhook URL 存入同一仓库的 Actions secret `FEISHU_WEBHOOK`。只要有 webhook 就启用飞书，不需要邮件凭据。仅接受 `https://open.feishu.cn/open-apis/bot/v2/hook/机器人标识`，禁止其他域名、端口、查询参数、片段及重定向。webhook 本身就是凭据，不要在聊天、issue 或日志中公开。

若机器人启用了签名校验，将签名密钥设置为 `FEISHU_SIGN_SECRET`；未启用签名时可不设置。签名使用秒级 timestamp，算法为 `Base64(HMAC-SHA256(key=timestamp + "\n" + secret, msg=b""))`。仅设置签名密钥而没有 webhook 会提示配置不完整。若使用关键词校验，请设置为固定正文关键词 **汽车技术日报**。

以 UTF-8 JSON 发送 `text` 消息，请求超时 20 秒，必须 HTTP 200 且 JSON 整数 `code == 0` 才视为成功；不打印 URL、响应正文或异常原文。发送不自动重试，避免在结果不明时立即重复投递。每渠道每日一次成功投递；飞书每次最多 12 篇，实际 JSON 请求体严格小于 20 KiB。标题最多 60 字符、来源 20 字符、摘录 80 字符；单条链接超过 600 UTF-8 字节或无效时明确提示查看仓库完整记录，不静默删除文章。若整批仍超限会缩小批次，仅本次成功批次标 sent，剩余留待下次日报，或明确选择手动 `send` 再发下一批。

## 时间和手动操作

按 `Asia/Shanghai` 计算日报日期：当天 **12:30** 后，某渠道当天尚未成功发送且有该渠道 pending 才发送；每两小时采集也能延迟补发。各渠道独立记录发送日期，失败渠道稍后可以补发，不重复成功渠道。日报包括此前未发送的 pending，可能跨多个日期；当天已发后的新记录及超批次剩余记录留到下一天。无内容不发。GitHub 两条 UTC cron 为 `17 */2 * * *` 和 `30 4 * * *`，后者对应北京时间 12:30 左右。

在 Actions → Auto tech daily → Run workflow 选择：

- `run`：正常采集和按时间判断发送。
- `preview`：拉取并预览，不注入任何投递 secrets、不修改状态、不提交、不发邮件或飞书消息。
- `send`：明确请求各已启用渠道强制发送自身 pending，绕过时间和当日限制，不重播历史 sent/skipped；飞书仍受每批上限约束。本地等价命令为 `python daily.py --force-send`。**这会实际发邮件或飞书消息，仅在确认需要时操作。** 与 `--dry-run` 同用仍不会投递。

本次双渠道改造验证全部投递均使用 mock，没有真实发邮件或飞书消息；此前邮件成功记录继续保留。

GitHub 定时任务可能排队、延迟或遗漏，不能保证准点；公开仓库连续 60 天无活动可能被停用 schedule，需在 Actions 检查并重新启用。免费额度及服务规则以 GitHub 当前政策为准。默认分支需允许 Actions 写入状态；分支保护阻止机器人 push 时任务明确失败，所有者应调整规则或授权，不要忽略失败。

## 状态、隐私与投递语义

`data/state.json` 不存邮箱、授权码、webhook 或签名密钥，但公开保存文章来源、链接、标题、原文摘要、发现时间、各渠道发送状态和发送日期；公开仓库的 Git 历史会继续保留旧版本。不要将私人 RSS 或私人文章写入配置。HTML 摘录去标签后再转义，文章链接仅允许 HTTP/HTTPS。

v1 → v2 迁移仅由程序运行时进行，本次发布不携带迁移后的状态，避免覆盖远程新文章。v2 的 `last_sent_dates` 分别记录 email/feishu 日期，文章 `delivery` 分别记录两渠道 pending/sent/skipped/discarded；顶层 status 仅作为队列汇总。旧 sent 视作两渠道历史已完成，不重发邮件也不重播飞书；旧 last_sent_date 只继承为邮件日期，飞书日期为空。旧 pending 保留邮件 pending，飞书标 skipped；旧 discarded 不重新入队。新文章在采集时为启用渠道记 pending，未启用渠道记 skipped。因此迟配飞书从启用后新采集的文章开始，迟配邮件同理；停用前已有 pending 不会自动清除，重新启用可以继续发送这些 pending。没有渠道配置时采集的文章仅保留去重记录，不自动追补。

GUID（按来源命名空间）、Atom ID 和文章链接的 SHA-256 用于稳定去重。记录自发现起保留 90 天，pending 最多 500 篇，超限时最早 pending 标为 discarded 并清除摘要，保留去重信息至到期。首次成功采集仅接纳近 48 小时有可解析日期的文章；首次缺日期条目跳过，后续无日期的新发现按发现时间入队。无时区日期按 UTC；异常超前超过 24 小时的文章忽略。首次部分源失败后仍完成初始化，恢复的源可能带入 90 天内较旧文章；可按需提高筛选条件。

各渠道成功发送后才标自身 sent，一个渠道失败保留自身 pending，另一渠道成功标记仍保存。工作流即使采集或投递步骤失败也尝试仅提交已变化的 `data/state.json`，普通 commit/push；没有变化不提交。state push 不触发采集循环，代码 push 仅运行测试。所有 workflow 共享 concurrency，运行中的任务不取消；GitHub 可能合并排队等待的运行，因此不能当作可靠消息队列。

SMTP 接受邮件或飞书接受消息后，进程崩溃、状态写入失败、commit 或 push 失败，后续重跑可能重复投递；网络断线也可能造成投递结果不确定。本项目提供持久化去重和失败重试，**不保证 exactly once**。发生 push 失败时先检查已发送邮件、群消息和任务日志，再决定是否重跑；不要盲目强制发送。

工作流不监听 `pull_request`，不会为 PR 加载邮件 secrets；仅采集 job 获得 `contents: write`。若仓库将来接受不可信代码，合并前必须审查，因为默认分支代码可读取 Actions secrets。
