# Ubuntu 24.04：手机网页与一个提醒渠道

这是人工部署步骤与模板。按下载/升级、测试、迁移状态、生成网页、验证nginx、启用提醒的顺序执行。代码使用 Python 3.12 标准库，无需 pip。

## 1. 下载与无发送验证

以 `ubuntu` 用户操作，代码固定放在 `/home/ubuntu/auto-tech-daily`。没有目录时用下面的克隆命令；已经克隆旧版时用后面的升级段，不删除、不覆盖状态。GitHub 不通时先反馈下载错误，不使用不明镜像，也不把凭据写进下载 URL。

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates
test ! -e "$HOME/auto-tech-daily" && test ! -L "$HOME/auto-tech-daily" && \
  GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch main \
  https://github.com/WweiYweiYW/auto-tech-daily.git "$HOME/auto-tech-daily"
```

已有旧克隆的升级：若服务器timer已经运行，先停止timer并等待当前service执行结束，再备份 `/var/lib/auto-tech-daily/state.json`；首次尚未安装服务时直接升级。下面仅在origin正确、main分支和工作区完全干净时fetch并快进，包含仓库内state的任何本地变更或冲突都停止，不reset、不自动stash。生产状态位于仓库外，不参与merge，也不从仓库重新覆盖它。

```bash
(
  set -e
  cd /home/ubuntu/auto-tech-daily
  test "$(git rev-parse --show-toplevel)" = "$PWD"
  origin=$(git remote get-url origin)
  case "$origin" in
    https://github.com/WweiYweiYW/auto-tech-daily.git|https://github.com/WweiYweiYW/auto-tech-daily|git@github.com:WweiYweiYW/auto-tech-daily.git) ;;
    *) echo 'origin不符合预期，停止升级。'; exit 1 ;;
  esac
  test "$(git branch --show-current)" = main
  git status --short
  if [ -n "$(git status --porcelain)" ]; then
    echo '有本地变更，停止升级；先核对和备份，不能reset覆盖。'
    exit 1
  fi
  GIT_TERMINAL_PROMPT=0 git fetch origin main
  git merge --ff-only origin/main
  git log -1 --oneline
)
```

下载或升级成功后运行全部现有与新增测试（不要只跑旧测试文件）：

```bash
(
  cd /home/ubuntu/auto-tech-daily || exit 1
  python3 -m unittest discover -s tests -v
  test_status=$?
  printf 'TEST_EXIT=%s\n' "$test_status"
  exit "$test_status"
)
```

测试成功后预览 RSS；不需要任何凭据。`--dry-run` 不写状态、不生成网站、不发提醒。此步骤读仓库自带状态去重，新增为零也可能正常。一个源失败时可能返回0，应检查统计是否2/2。

```bash
(
  cd /home/ubuntu/auto-tech-daily || exit 1
  umask 077
  log=$(mktemp /tmp/auto-tech-preview.XXXXXX.log) || exit 1
  env -u SMTP_USERNAME -u SMTP_PASSWORD -u MAIL_TO \
      -u FEISHU_WEBHOOK -u FEISHU_SIGN_SECRET -u WEB_OUTPUT_DIR \
      -u WEB_BASE_URL -u WEB_REMINDER_CHANNEL -u DAILY_STATE_PATH \
      -u GITHUB_STEP_SUMMARY python3 daily.py --dry-run >"$log" 2>&1
  result=$?
  sed -n -e '/^采集新增 /p' -e '/^源失败：/p' -e '/^全部 RSS/p' \
      -e '/^预览：/p' -e '/^运行失败/p' "$log"
  printf 'DRY_RUN_EXIT=%s\n日志：%s\n' "$result" "$log"
  exit "$result"
)
```

## 2. 单独验证网页（仍无发送）

先执行测试并确认 `web_ui.py` 已包含在下载版本中。可以在个人目录生成临时网页验证渲染；以下直接调用导出函数，不采集、不改变状态、不加载提醒配置。

```bash
(
  cd /home/ubuntu/auto-tech-daily || exit 1
  python3 -c 'from datetime import datetime, timezone; import daily, web_digest; web_digest.publish(daily.load_state(daily.ROOT / "data/state.json"), datetime.now(timezone.utc), "/home/ubuntu/auto-tech-daily-site-preview")'
)
```

生成 `/home/ubuntu/auto-tech-daily-site-preview/index.html`。只发布文章字段，不发布 delivery、发送日期表、凭据或状态 JSON。此时不要启动生产 service。

## 3. 准备持久目录与模板（不启用）

```bash
sudo install -d -o ubuntu -g ubuntu -m 0755 /var/lib/auto-tech-daily
sudo install -d -o ubuntu -g ubuntu -m 0755 /var/lib/auto-tech-daily/site
sudo install -o root -g root -m 0644 /home/ubuntu/auto-tech-daily/deploy/auto-tech-daily.service /etc/systemd/system/auto-tech-daily.service
sudo install -o root -g root -m 0644 /home/ubuntu/auto-tech-daily/deploy/auto-tech-daily.timer /etc/systemd/system/auto-tech-daily.timer
if ! sudo test -e /etc/auto-tech-daily.env; then
  sudo install -o root -g root -m 0600 /home/ubuntu/auto-tech-daily/deploy/auto-tech-daily.env.example /etc/auto-tech-daily.env
fi
sudo systemctl daemon-reload
systemd-analyze calendar '*-*-* 12:30:00 Asia/Shanghai'
systemd-analyze calendar '*-*-* 00/2:17:00 Asia/Shanghai'
```

这一步不执行 `enable` 或 `start`。环境文件留空的 `WEB_BASE_URL` 会使生产检查失败，避免误用旧完整日报模式。系统级 manager 以 root 读取0600文件后注入 `User=ubuntu` 进程；不要因此把环境文件改成全员可读，不要用 `source` 后打印环境。

生产预检查还要求：已有普通非软链接state文件，0600、ubuntu可读写、所在目录可写，内容可迁移且schema有效；没有state就拒绝启动，不自动初始化。site必须是非软链接目录、具备others读取和穿越权限（0755），每一级父目录具备others穿越权限；预生成的index.html必须是普通非软链接文件且nginx可读（0644）。不要将state改成0644来解决网站权限。

系统 timer 每两小时17分采集，12:30主执行，12:37与12:47补偿；时区显式指定上海。`Persistent=true` 在开机后补一次错过的执行，不逐次重放；`AccuracySec=1s` 不是网络发送准点保证。每个渠道每日成功一次逻辑仍有效。

## 4. 迁移状态与防止双端投递

正式迁移时先在 GitHub Actions 停用 **Auto tech daily** 工作流的自动执行，并确认已有运行全部结束；不要同时在 GitHub 手动 send。服务器 timer/service 也保持停止。然后获取该时刻最新 main 状态到一个新的临时克隆，不能使用早先下载的陈旧状态：

```bash
(
  set -e
  migration=$(mktemp -d /tmp/auto-tech-final-state.XXXXXX)
  GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch main \
    https://github.com/WweiYweiYW/auto-tech-daily.git "$migration/repo"
  python3 -c 'import json,sys; s=json.load(open(sys.argv[1],encoding="utf-8")); assert s.get("version",1) in (1,2) and isinstance(s["items"],dict)' "$migration/repo/data/state.json"
  if sudo test -e /var/lib/auto-tech-daily/state.json; then
    echo '生产状态已存在：停止，需人工核对来源，禁止覆盖。'
    exit 1
  fi
  sudo install -o ubuntu -g ubuntu -m 0600 "$migration/repo/data/state.json" /var/lib/auto-tech-daily/state.json
  printf '迁移来源：'
  git -C "$migration/repo" log -1 --format=%H
)
```

`DAILY_STATE_PATH` 直接指向 `/var/lib/auto-tech-daily/state.json`，不使用软链接：程序原子 replace 会替换软链接本身。以后更新代码不更新此持久文件，不 `git reset --hard` 或 `git clean` 清理生产目录。更新前停timer、等待service结束并备份生产状态，审查代码差异后再更新；不能把 `git pull` 当作状态迁移。服务器状态不自动提交 GitHub。

## 5. nginx 仅发布静态页面

以下用于新建且尚无其他站点的服务器；已有网站先人工合并配置，避免两个 default_server 冲突。只允许访问 `/` 和 `/index.html`，其余路径404，无目录列表；临时写入文件、项目源码和状态不公开。

```bash
sudo apt-get install -y nginx
sudo install -o root -g root -m 0644 /home/ubuntu/auto-tech-daily/deploy/nginx-auto-tech-daily.conf /etc/nginx/sites-available/auto-tech-daily
if [ -L /etc/nginx/sites-enabled/default ]; then
  sudo unlink /etc/nginx/sites-enabled/default
fi
sudo ln -s /etc/nginx/sites-available/auto-tech-daily /etc/nginx/sites-enabled/auto-tech-daily
sudo nginx -t
```

先用已迁移状态生成生产静态页，不采集、不投递：

```bash
(
  cd /home/ubuntu/auto-tech-daily || exit 1
  python3 -c 'from pathlib import Path; from datetime import datetime, timezone; import daily, web_digest; web_digest.publish(daily.load_state(Path("/var/lib/auto-tech-daily/state.json")), datetime.now(timezone.utc), "/var/lib/auto-tech-daily/site")'
)
```

生成成功后，先验证配置再启动/重载nginx，并检查云安全组与服务器防火墙的80端口。检查静态根为 `/var/lib/auto-tech-daily/site`，**不能改为项目根或 `/var/lib/auto-tech-daily`**。

```bash
sudo nginx -t && sudo systemctl enable --now nginx.service
sudo nginx -t && sudo systemctl reload nginx.service
curl --fail --silent --show-error --output /dev/null http://127.0.0.1/index.html
```

最后一条成功表示本机nginx能读取index；再用手机访问入口验证外网连通。service通过 `Requires=nginx.service` 和 `After=nginx.service` 确保nginx先启动；这不替代首次的 `nginx -t` 或公网连通检查。

公网IP的HTTP只用于临时连通性测试，文章会公开且传输未加密。正式使用另行配置自己的域名与HTTPS；中国大陆服务器使用域名建站需按腾讯云及监管要求办理备案。不要在HTTP上配置需要输入秘密的Basic Auth。模板不包含IP，不配置TLS或访问控制。

## 6. 配置一个提醒渠道并启用

仅在服务器本地执行 `sudoedit /etc/auto-tech-daily.env` 填写。不要把邮箱授权码、webhook、签名密钥贴进聊天、仓库、截图或命令行参数。

- 必填 `WEB_BASE_URL` 为手机可访问的实际入口，与这个静态站点一致。只支持 `http(s)://主机`、`http(s)://主机/` 或 `http(s)://主机/index.html`，不支持 `/daily/` 等自定义路径、query、fragment或userinfo；无有效地址禁止启用提醒服务。普通文章来源链接仍允许正常query。
- 默认 `WEB_REMINDER_CHANNEL=email`，配置SMTP用户名和授权码；`MAIL_TO`可选。
- 改飞书时只把选择改成 `feishu`，配置webhook与可选签名。即使两份凭据都在也只发所选渠道。
- 所选渠道缺少凭据时，核心程序在采集前直接失败，不改状态或网页。原子发布失败则不发提醒、保留采集状态并返回非零；全部源采集失败时保留旧网页字节与更新时间，不发布新页。选中渠道发送失败保留pending；成功后按日期去重。未选择渠道已有pending不被抹掉，skipped不补播；切换渠道可能发送那个渠道先前留下的pending。
- 提醒只计入本次实际网页展示的待发文章；非法或超长链接被网页过滤时，不计数、不标sent，保留pending并记录过滤数量。网页展示整个保留期内的文章，包括sent/skipped；飞书网页提醒一条覆盖符合条件的全队列，不套用旧版12篇上限，仍限制20KiB。

确认GitHub已停、最终状态已迁移、网页能用、环境文件路径及权限正确后，按下面顺序启动一次service、检查结果，再启用timer。启动正常模式若已经过12:30且存在pending，**会真实发送提醒**；尚未完成前述步骤就不要跳到这里。不要用手动 `--force-send` 验证迁移，不要重新启用GitHub和服务器双端采集。

```bash
sudo systemctl daemon-reload
sudo nginx -t && sudo systemctl start auto-tech-daily.service
sudo systemctl status auto-tech-daily.service --no-pager
sudo journalctl -u auto-tech-daily.service --no-pager -n 30
```

oneshot执行完成后显示inactive可以正常；检查执行结果为 `status=0/SUCCESS`，并确认日志的网页与提醒结果。service失败先处理错误，不启用timer；成功后：

```bash
sudo systemctl enable --now auto-tech-daily.timer
systemctl list-timers auto-tech-daily.timer
```

运行检查可用 `sudo systemctl status auto-tech-daily.service`、`systemctl list-timers auto-tech-daily.timer` 与 `sudo journalctl -u auto-tech-daily.service --no-pager -n 30`。维护手动运行也应走同一service以共享flock；同一service本身不会并发启动。服务5分钟超时或中断的投递结果可能不确定，先看日志再决定重跑，仍不承诺exactly-once。
