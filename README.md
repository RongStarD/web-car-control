# OHCar Web 控制台

面向 Jetson + ROS 2 Foxy 小车的 Web 进程编排与可视化控制台。当前实现以真实 ROS 节点为管理单元，不调用 `m1/m2/m3/n1/n2...` 等历史快捷命令，也不启动 RViz；地图、代价地图、路径、雷达和运行状态均由浏览器绘制。

## 当前状态

- 已通过 SSH 只读核对 `nifty_dirac` 和 `sharp_maxwell` 中的节点、话题、Launch 源码与旧管理代码。
- 已实现原子组件与功能点配置注册表，旧雷达行为保留但从操作界面隐藏。
- 已实现 Docker/进程、ROS 图、Lifecycle、数据面的分层监控。
- 已实现速度仲裁、人工控制心跳、断连锁停和跨容器硬件互斥。
- 已实现 React Web 控制台和本地模拟数据源。
- 已实现地图点位、默认位姿、路线档案和逐点任务执行。

实车控制台地址：[http://10.39.132.165:8080](http://10.39.132.165:8080)。

详细盘点见 [Jetson小车节点与功能映射方案.md](./Jetson小车节点与功能映射方案.md)。

## 目录

```text
web/config/system.json       原子组件与功能点注册表
web/backend/icar_web         Host Orchestrator、API、WebSocket、健康监控
web/ros/managed_process.sh   容器内 PID/PGID 进程执行器
web/ros/velocity_arbiter.py  唯一 /cmd_vel 输出仲裁节点
web/ros/motion_convention.py 底盘硬件话题与 ROS 标准话题隔离适配
web/ros/icar_ros_bridge.py   ROS 与 Web JSON 协议中介
web/frontend                 React 功能区与 Canvas 可视化
web/deploy                   Jetson 部署脚本和 systemd 单元
```

## 本地演示

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r web\backend\requirements.txt
cd web\frontend
npm.cmd install
npm.cmd run build
cd ..\backend
..\..\.venv\Scripts\python.exe -m icar_web --demo
```

浏览器打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。演示模式使用与实车相同的 API、功能切换和 WebSocket 协议，只替换 Docker/ROS 数据源。

运行测试：

```powershell
cd web\backend
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 连接状态

- `Web 服务在线`：浏览器能够访问 Jetson API，仅说明管理服务和网络可达。
- `设备已识别`：宿主机能够看到 `/dev/myserial`、`/dev/rplidar`、`/dev/video0`，不等同于 ROS 节点已经打开设备。
- `ROS bridge 在线`：选择功能后，中介进程已进入对应容器并可交换 ROS 数据。
- `小车在线`：功能处于活动状态，且 `/voltage` 遥测持续更新。`IDLE` 时不会用这个标签伪装连接成功。

地图画布采用屏幕坐标拖动语义：向上拖动时地图向上移动，已修正此前纵向方向相反的问题。

## 地图点位与任务

- 建图模块中选择“标记点位”，在地图上从点位向朝向方向拖动，松开后填写名称并添加。
- 每张地图至少指定一个默认位姿。保存地图时，栅格地图写入 Jetson `/home/jetson/maps`，点位、默认位姿和路线写入同名 `.ohcar.json` 文件。
- “任务”功能从已记录点位中编排路线，支持重复点位和上下排序。路线执行时只有当前点 Nav2 返回成功才会提交下一个点；拒绝、失败、取消或急停都会终止剩余路线。
- 建图、DWA、TEB 和任务在侧栏选中后不会立刻启动，必须在右侧功能控制区再次确认。
- 导航或任务选用带默认位姿的地图时，会在节点全部就绪后自动发布默认位姿；定位未确认前不能发送目标或启动路线。
- 默认位姿发布后会请求 AMCL 静止扫描更新，并等待 `/amcl_pose` 回传；确认定位后自动清理局部和全局代价地图，路线按钮才会解锁。
- 手动设置初始位姿时可用角度条精调 `-180°～180°` 朝向，地图箭头会同步预览。

## 安全语义

- 所有运动功能全局互斥；切换容器前先锁停，再停止旧进程和旧容器。
- `/driver_node` 只接收仲裁器输出的 `/cmd_vel`。
- 运动约定适配节点保持原始 ROS 符号并隔离硬件话题；控制指令活跃时使用带底盘响应时间常数的速度驱动开环里程计，解决硬件速度反馈持续为零时车体 TF 不更新、仿真转向抢跑的问题，指令停止后再回退到新鲜硬件反馈。雷达相对底盘的朝向由 `base_link -> laser` 静态 TF 单独定义。
- Web 中的车体位姿和雷达点使用各自 ROS 消息时间查询 TF，禁止用最新车体角度变换较早的雷达帧；平面航向统一规范到 `[-π, π]`，避免等价四元数换号造成角度数值跳变。
- Web、Nav2 和行为节点分别写入 `/cmd_vel/manual`、`/cmd_vel/nav`、`/cmd_vel/behavior`。
- 人工速度超过 350ms 未续期自动归零；最后一个 Web 控制端断开时锁停。
- 服务重启只认领进程，不自动恢复运动。认领后进入 `ERROR` 并保持急停，等待人工确认。
- 进程退出和容器退出是硬故障；话题新鲜度只用于 `READY/DEGRADED`，不伪装成进程状态。

## Jetson 部署

部署脚本不会自动启动任何功能节点。它会拒绝在容器存在非 shell 进程时覆盖容器脚本。

1. 将项目放到 Jetson，例如 `/home/jetson/ohcar`。
2. 确认 `web/frontend/dist` 已构建。
3. 在 Jetson 执行：

```bash
cd /home/jetson/ohcar
bash web/deploy/install_on_jetson.sh
```

安装完成后服务地址为 `http://<Jetson-IP>:8080`。服务开机自启但保持 `IDLE`，只有操作员二次确认后才会启动对应原子节点组合。正式实车验收顺序应为：待机与急停、人工低速、建图、点位与地图保存、默认位姿定位、单点导航、路线任务；旧雷达行为和视觉跟随当前不在操作界面显示。

## CI/CD

GitHub Actions 会在 Pull Request 和 `main` 推送时运行后端测试、ROS 脚本检查和前端生产构建。自动部署使用 Jetson 自托管 runner，并由仓库变量 `JETSON_CD_ENABLED` 显式启用；小车存在活动 ROS 进程时部署会拒绝执行。

完整配置、runner 注册、安全边界和回滚方法见 [CI/CD 配置](./docs/CICD.md)。
