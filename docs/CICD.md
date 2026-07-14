# CI/CD 配置

## 流程

`.github/workflows/pipeline.yml` 提供一条完整流水线：

- Pull Request：在 GitHub 托管 runner 上执行后端测试、ROS/Python/Shell 语法检查和前端构建。
- `main` 推送：执行相同检查并上传 `frontend-dist` 构建产物。
- Jetson 部署：仅当仓库变量 `JETSON_CD_ENABLED` 等于 `true` 时运行，并且只接收 `main` 的可信提交。
- 手动重试：在 Actions 页面运行 `CI/CD` 的 `workflow_dispatch`。

部署前会检查 `nifty_dirac` 和 `sharp_maxwell`。只要任一运行中的容器存在非 shell 进程，部署立即失败，不复制文件，也不停止小车功能。

## 注册 Jetson Runner

仓库管理员需要完成一次 runner 注册。GitHub 要求用户仓库的 owner 或具有 admin 权限的成员创建仓库级 runner。

1. 打开仓库 `Settings > Actions > Runners > New self-hosted runner`。
2. 选择 `Linux` 和 `ARM64`，在 Jetson 的 `/home/jetson/actions-runner` 按页面命令安装。
3. 执行 `config.sh` 时增加自定义标签 `jetson`，并将 runner 注册为 `jetson` 用户的 systemd 服务。
4. 确认 runner 同时具有 `self-hosted`、`Linux`、`ARM64`、`jetson` 标签。

当前 Actions 使用 Node 24 运行时。自托管 runner 必须不低于 GitHub 页面要求的最低版本，并保持自动更新。

## 首次启用 CD

确保小车处于 `IDLE`，在 Jetson 当前生产目录执行：

```bash
cd /home/jetson/ohcar
bash web/deploy/install_on_jetson.sh --enable-ci
```

该命令执行正常安装，并额外安装 root 所有的 `/usr/local/sbin/ohcar-web-restart`。sudoers 只允许 runner 用户无密码执行这个固定 helper；helper 只能重启 `ohcar-web.service`，不能执行任意命令。

随后由仓库管理员配置：

1. 在 `Settings > Environments` 创建 `jetson-production`，限制部署分支为 `main`。
2. 在 `Settings > Secrets and variables > Actions > Variables` 创建 `JETSON_CD_ENABLED=true`。
3. 推送一次提交或在 Actions 页面手动运行 `CI/CD`。

不需要保存 Jetson SSH 密码或 sudo 密码到 GitHub Secrets。runner 通过出站 HTTPS 主动连接 GitHub。

## 部署目录与回滚

runner 的临时 checkout 不作为生产目录。`deploy_from_ci.sh` 会先完成空闲检查，再将已审核源码和前端 artifact 同步到 `/home/jetson/ohcar`，保留 `.venv`、本地压缩包和 `node_modules`，然后更新两个 ROS 容器并重启 Web 服务。

部署失败时：

- 如果日志显示容器存在活动进程，先在 Web 控制台停止功能，再从 Actions 页面重新运行失败任务。
- 如果需要回滚，优先在 `main` 上 `git revert` 对应提交；CI 通过后 CD 会部署回滚提交。
- 紧急情况下可在 Jetson 检出指定提交，重新构建前端并执行普通安装脚本。

## 安全边界

该仓库是公开仓库，自托管 runner 不能用于 Pull Request 作业。当前工作流的 PR 任务全部运行于 `ubuntu-latest`；只有 `main` 推送且开关启用时，Jetson runner 才会收到部署任务。

参考：

- [GitHub：添加自托管 runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
- [GitHub：公共仓库的自托管 runner 风险](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access)
- [GitHub：自托管 runner 维护要求](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
