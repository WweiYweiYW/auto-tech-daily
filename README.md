# 汽车技术日报

独立 Python 3.12 项目：免费 RSS、关键词筛选、原文摘录、QQ 邮件。没有付费 AI、没有 NAS、没有外部发信 Action。运行于公开 GitHub 仓库的 `ubuntu-latest` 标准 runner，应用只使用 Python 标准库，Linux 自带 IANA 时区数据库。

## 发布与运行

由仓库所有者创建独立公开仓库 `WweiYweiYW/auto-tech-daily`，将本目录内容放在仓库根目录，默认分支设为 `main`。本实现阶段没有创建远程、提交或推送。

```sh
python -m unittest discover -s tests -v
python daily.py --dry-run
python daily.py
```

Windows 如果缺少系统 IANA 时区数据库，可在自己的虚拟环境安装 `tzdata`；生产 Ubuntu 无需 pip 安装。`--dry-run` 会实际拉取源，输出不含邮件地址配置的文章预览，既不写状态也不发信。不要将预览重定向后提交仓库。

未配置邮件凭据时，正常运行只采集并提示“未配置”，不承诺已经发送。两个源全失败返回非零；一个源失败仍处理其余源。每源 20 秒超时，最多尝试 3 次，读取上限 5 MiB。关键词在 `config.json` 修改：IT之家必须同时具有汽车语境，Ars Cars 使用专门汽车源语境；优惠促销排除优先。该规则可能漏报或误报，不保证完整覆盖。

## QQ 邮件配置

登录 QQ 邮箱，依官方帮助 <https://service.mail.qq.com/> 开启 SMTP 服务并获取授权码。使用 `SMTP_SSL` 连接 `smtp.qq.com:465`。这里填写授权码，而不是 QQ 登录密码；不要在聊天、issue、日志或代码中分享授权码。

在 [仓库 Actions secrets 设置](https://github.com/WweiYweiYW/auto-tech-daily/settings/secrets/actions) 中添加：

- `SMTP_USERNAME`：发件 QQ 邮箱。
- `SMTP_PASSWORD`：QQ 邮箱 SMTP 授权码。
- `MAIL_TO`：可选，收件邮箱；省略则发给发件邮箱自身。建议只填写一个有效地址。

两项 SMTP 凭据齐全后，发送错误会使任务失败，并保留待发送记录；日志不打印邮箱配置、授权码或 SMTP 异常原文。不要把 `.env`、凭据文件或邮箱地址写入代码。程序不自动读取 `.env`，本地配置需设置环境变量。

## 时间和手动操作

按 `Asia/Shanghai` 计算日报日期：当天 08:37 后且当天尚未成功发送，有 pending 才发送；每两小时采集也能补发。日报包括此前未发送的 pending，可能跨多个日期；当天已发后的新记录留到下一天。无内容不发。GitHub 两条 UTC cron 为 `17 */2 * * *` 和 `37 0 * * *`，后者对应北京时间 08:37。

在 Actions → Auto tech daily → Run workflow 选择：

- `run`：正常采集和按时间判断发送。
- `preview`：拉取并预览，不注入邮件 secrets、不修改状态、不提交、不发信。
- `send`：明确请求强制发送 pending，绕过时间和当日限制；仍需有内容和已配置凭据。本地等价命令为 `python daily.py --force-send`。**这会实际发信，仅在确认需要时操作。** 与 `--dry-run` 同用仍不会发信。

本阶段验证仅使用 mock SMTP 和无发送的实际采集，没有真实发信。

GitHub 定时任务可能排队、延迟或遗漏，不能保证准点；公开仓库连续 60 天无活动可能被停用 schedule，需在 Actions 检查并重新启用。免费额度及服务规则以 GitHub 当前政策为准。默认分支需允许 Actions 写入状态；分支保护阻止机器人 push 时任务明确失败，所有者应调整规则或授权，不要忽略失败。

## 状态、隐私与投递语义

`data/state.json` 不存邮箱或授权码，但公开保存文章来源、链接、标题、原文摘要、发现时间、发送状态和发送日期；公开仓库的 Git 历史会继续保留旧版本。不要将私人 RSS 或私人文章写入配置。HTML 摘录去标签后再转义，文章链接仅允许 HTTP/HTTPS。

GUID（按来源命名空间）、Atom ID 和文章链接的 SHA-256 用于稳定去重。记录自发现起保留 90 天，pending 最多 500 篇，超限时最早 pending 标为 discarded 并清除摘要，保留去重信息至到期。首次成功采集仅接纳近 48 小时有可解析日期的文章；首次缺日期条目跳过，后续无日期的新发现按发现时间入队。无时区日期按 UTC；异常超前超过 24 小时的文章忽略。首次部分源失败后仍完成初始化，恢复的源可能带入 90 天内较旧文章；可按需提高筛选条件。

成功发送后才标 sent，SMTP 失败保留 pending 并保存。工作流即使采集或 SMTP 步骤失败也尝试仅提交已变化的 `data/state.json`，普通 commit/push；没有变化不提交。state push 不触发采集循环，代码 push 仅运行测试。所有 workflow 共享 concurrency，运行中的任务不取消；GitHub 可能合并排队等待的运行，因此不能当作可靠消息队列。

SMTP 接受邮件后，进程崩溃、状态写入失败、commit 或 push 失败，后续重跑可能重复投递；SMTP 的断线也可能造成投递结果不确定。本项目提供持久化去重和失败重试，**不保证 exactly once**。发生 push 失败时先检查已发送邮件和任务日志，再决定是否重跑；不要盲目强制发送。

工作流不监听 `pull_request`，不会为 PR 加载邮件 secrets；仅采集 job 获得 `contents: write`。若仓库将来接受不可信代码，合并前必须审查，因为默认分支代码可读取 Actions secrets。
