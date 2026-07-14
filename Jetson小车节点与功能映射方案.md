# Jetson 小车节点、话题与功能映射方案

更新时间：2026-07-14
盘点方式：SSH 只读检查 Jetson 宿主、两个容器文件系统、Launch 源码、功能源码和历史日志。盘点过程未启动、停止或修改任何实车 ROS 功能。

## 1. 执行口径

历史快捷命令 `m1/m2/m3/n1/n2...` 只作为追溯现有系统行为的证据，不进入新系统配置，也不作为进程管理对象。

新系统采用两级注册表：

1. 原子组件：所属容器、直接进程入口、真实 ROS 节点名、话题、依赖、Lifecycle 节点和硬件资源。
2. 功能点：引用原子组件，由依赖图展开完整节点组合。

`RViz`、键盘遥控、`joint_state_publisher_gui` 等显示或桌面交互节点不进入功能组合。地图、代价地图、路径、雷达和状态在 Web 前端的独立功能区显示。

## 2. 当前实车状态

| 对象 | 2026-07-14 最后观察状态 | 说明 |
|---|---|---|
| Jetson | `10.39.132.165`，Python `3.8.10` | `ohcar-web.service` 已在宿主层运行，监听 8080 |
| `nifty_dirac` | Docker running，仅 `/bin/bash` | Yahboom 底盘、建图、Nav2 环境；无 ROS 功能进程 |
| `sharp_maxwell` | Docker running，仅 `/bin/bash` | iCar 雷达行为和视觉环境；无 ROS 功能进程 |
| `/dev/myserial -> ttyUSB1` | 存在 | 两个容器共享的底盘串口 |
| `/dev/rplidar -> ttyUSB0` | 存在 | 两个容器共享的 A1 雷达 |
| `/dev/video0` | 存在 | 视觉功能共享视频设备 |
| `/opt/icar-web/ros` | 两容器均存在 | 仅放置受管执行器、速度仲裁器和 ROS 中介脚本 |
| 宿主直连桥 | 未运行 | `/home/jetson/rosmaster-control-bridge`，9093/9094 |

两个容器绑定同一组硬件。即使 Docker 都是 `running`，运动功能也必须全局互斥。

部署后 `GET /api/preflight` 返回 `environment=live`、`feature=IDLE`、`overall=OK`；`/dev/myserial`、`/dev/rplidar` 和 `/dev/video0` 均存在。Web 管理服务处于活动状态，但两只容器仍只有 `bash`，ROS bridge 未启动，符合“管理面在线、功能面待机”的预期。

## 3. Yahboom 导航容器

### 3.1 底盘、里程计与雷达

| 原子组件 | 真实节点 | 订阅 | 发布/提供 |
|---|---|---|---|
| X3 驱动 | `/driver_node` | `/cmd_vel`、`/RGBLight`、`/Buzzer` | `/vel_raw`、`/voltage`、`/edition`、`/joint_states`、`/imu/data_raw`、`/imu/mag` |
| 里程计 | `/base_node` | `/vel_raw` | `/odom_raw`；`pub_odom_tf=false` |
| IMU 滤波 | `/imu_filter_madgwick` | `/imu/data_raw` | `/imu/data` |
| EKF | `/ekf_filter_node` | `/odom_raw`、IMU | `/odom`、`odom -> base_footprint` TF |
| 机器人模型 | `/robot_state_publisher` | `/joint_states` | `/tf`、`/tf_static` |
| A1 雷达 | `/sllidar_node` | `/dev/rplidar` | `/scan` |
| 雷达静态 TF | `/laser_static_tf` | 无 | `base_link -> laser` |

真实节点名是 `/driver_node` 和 `/base_node`，不是可执行文件名 `Mcnamu_driver_X3`、`base_node_X3`。

旧 Bringup 还会启动 `/joy_ctrl` 和 `joint_state_publisher`。Web 组合不启动它们：驱动已经发布 `/joint_states`，而 `/joy_ctrl` 会新增未经仲裁的 `/cmd_vel` 来源。

### 3.2 GMapping

源码确认 `map_gmapping_a1_launch.py` 只是包含底盘 Bringup 和下面这个单节点：

```bash
ros2 run slam_gmapping slam_gmapping
```

| 节点 | 订阅 | 发布/提供 |
|---|---|---|
| `/slam_gmapping` | `/scan`、TF | `/map`、`/map_metadata`、`map -> odom` TF、地图服务 |

因此 `SLAM` 由底盘、里程计、TF、雷达和 `/slam_gmapping` 的依赖闭包组成，不调用旧 `m1`。

### 3.3 Nav2

DWA 和 TEB 使用相同节点集合，不同之处是参数文件与局部规划插件：

- DWA：`dwa_nav_params.yaml`，`dwb_core::DWBLocalPlanner`
- TEB：`teb_nav_params.yaml`，`teb_local_planner::TebLocalPlannerROS`

| 节点 | Lifecycle | 关键输入/接口 | 关键输出/接口 |
|---|---:|---|---|
| `/map_server` | 是 | 地图 YAML | `/map`、地图服务 |
| `/amcl` | 是 | `/map`、`/scan`、`/initialpose`、TF | `/amcl_pose`、`map -> odom` TF |
| `/controller_server` | 是 | `/odom`、局部代价地图、FollowPath | `/cmd_vel/nav`、局部路径 |
| `/planner_server` | 是 | 全局代价地图、ComputePath | `/plan` |
| `/recoveries_server` | 是 | 局部代价地图、恢复 Action | `/cmd_vel/nav` |
| `/bt_navigator` | 是 | NavigateToPose | 调度规划、控制和恢复 |
| `/waypoint_follower` | 是 | FollowWaypoints | 调用 NavigateToPose |

新管理器直接启动每个进程，并按依赖顺序执行 `configure -> activate`。不启动旧的 `lifecycle_manager_*`，生命周期所有权归 Container Agent，避免双重管理。

## 4. iCar 行为容器

### 4.1 雷达行为

| 功能 | 节点 | 订阅 | 发布 |
|---|---|---|---|
| 驱动 | `/driver_node` | `/cmd_vel` | `/vel_raw`、`/voltage`、IMU、关节状态 |
| 雷达 | `/sllidar_node` | `/dev/rplidar` | `/scan` |
| 避障 | `/laser_Avoidance_a1` | `/scan`、`/JoyState` | `/cmd_vel/behavior` |
| 跟随 | `/laser_Tracker_a1` | `/scan`、`/JoyState` | `/cmd_vel/behavior` |
| 警戒 | `/laser_Warnning_a1` | `/scan`、`/JoyState` | `/cmd_vel/behavior`、`/Buzzer` |

源码中的节点名确实拼写为 `Warnning`。警戒节点会旋转小车，不是纯蜂鸣器告警。

### 4.2 视觉跟随

| 组件 | 节点 | 输入 | 输出 |
|---|---|---|---|
| Astra | `/camera/camera` | Astra USB | 深度、彩色、红外、点云和相机 TF |
| 颜色识别 | `/ColorIdentify` | 代码直接 `cv.VideoCapture(0)` | `/Current_point` |
| 颜色跟随 | `/ColorTracker` | 深度图、`/Current_point`、`/JoyState` | `/cmd_vel/behavior` |

`ColorIdentify` 与 Astra 可能争用同一视频设备。`VISUAL_TRACK` 已完整登记节点关系，但在配置中 `enabled=false`，修复为 ROS 图像订阅并完成实车验收前不允许启动。

## 5. 旧管理节点与中介节点状态

### 5.1 `/robot_app_api`

文件：`nifty_dirac:/tmp/robot_app_api.py`，792 行。它是普通 `rclpy.node.Node`，不是 LifecycleNode。

主要问题：

- 只管理导航容器中的旧 Launch 组合。
- `Popen` 句柄只在内存中，重启后不能认领进程。
- 使用宽泛 `pkill -f` 兜底，没有持久 PID/PGID 所有权。
- 用话题新鲜度推断进程状态，静态地图或短时抖动会误判。
- 急停只发一次零速度，其他 `/cmd_vel` 发布者可立即覆盖。

### 5.2 `/app_bridge`

文件：`nifty_dirac:/tmp/app_bridge.py`，499 行；WebSocket 9092。

可复用价值是地图、代价地图、路径和雷达的数据压缩思路。当前问题包括断连不停车、栅格包类型混用、坐标系处理不完整，以及直接发布最终 `/cmd_vel`。

容器内存在 2026-07-13 补丁包，但当前文件 SHA-256 与补丁前备份一致，补丁未应用。

## 6. 功能点与原子节点组合

| 功能点 | 容器 | 节点组合摘要 | 控制源 |
|---|---|---|---|
| `WEB_MANUAL` | `nifty_dirac` | 仲裁、驱动、里程计、IMU、EKF、模型、雷达、静态 TF | `MANUAL` |
| `SLAM` | `nifty_dirac` | `WEB_MANUAL` 基础节点 + `/slam_gmapping` | `MANUAL` |
| `NAV_DWA` | `nifty_dirac` | 基础节点 + map_server、AMCL、controller、planner、recoveries、BT、waypoints | `NAV` |
| `NAV_TEB` | `nifty_dirac` | 与 DWA 相同节点，使用 TEB 参数 | `NAV` |
| `LASER_AVOID` | `sharp_maxwell` | 仲裁、驱动、雷达、Avoidance | `BEHAVIOR` |
| `LASER_TRACK` | `sharp_maxwell` | 仲裁、驱动、雷达、Tracker | `BEHAVIOR` |
| `LASER_GUARD` | `sharp_maxwell` | 仲裁、驱动、雷达、Warnning | `BEHAVIOR` |
| `VISUAL_TRACK` | `sharp_maxwell` | 仲裁、驱动、Astra、ColorIdentify、ColorTracker | 禁用待整改 |

完整机器可执行映射位于 `web/config/system.json`。依赖图会自动补齐基础组件，浏览器只提交功能点 ID，不接收任意 Shell 命令。

## 7. 进程管理和状态监控

### 7.1 所有权

```text
Web UI
  -> Web Gateway / Host Orchestrator
     -> Docker target + hardware mutex
     -> managed_process.sh (PID/PGID/exit code/log)
     -> Nav2 lifecycle transitions
  -> ROS bridge -> visualization events and whitelisted commands
  -> velocity arbiter -> /cmd_vel -> /driver_node
```

每个原子进程使用独立 session/process group，停止顺序为 `SIGINT -> SIGTERM -> SIGKILL`。状态目录持久记录 PID、命令、启动时间、退出码和日志，不使用 `pkill -f` 主路径。

### 7.2 监控分层

| 层级 | 内容 | 作用 |
|---|---|---|
| L0 | Docker、PID/PGID、退出码 | 进程生死的主证据 |
| L1 | 真实 ROS 节点集合 | 检测节点缺失和重复 |
| L2 | Nav2 Lifecycle state | 检测未配置、未激活和 finalized |
| L3 | `/odom`、`/scan`、TF、地图、代价地图 | READY/DEGRADED 和传感器降级 |
| L4 | Action、地图保存产物、导航结果 | 具体功能验收 |

服务重启后只认领已有 PID，不恢复运动：仲裁模式强制 `IDLE`、急停保持锁存、业务状态进入 `ERROR`，必须由操作者重新选择功能。

## 8. Web 可视化与扩展

前端按 `surface` 注册功能模块：`overview`、`manual`、`mapping`、`navigation`、`behavior`、`vision`。服务器下发功能点、组件和节点关系，侧栏与状态清单自动生成。

当前画布支持主地图、全局/局部代价地图、路径、雷达点、机器人位姿、缩放和平移。未来实时状态监测只需新增 ROS bridge 事件与前端面板模块，不需要修改进程编排协议。

连接状态按管理服务、Docker、宿主硬件、ROS bridge 和底盘遥测分层显示。只有活动功能下持续收到 `/voltage` 遥测时才显示“小车在线”；`IDLE` 只显示设备存在和待激活验证。地图拖动使用屏幕坐标语义，纵向拖动方向已在 2026-07-14 修正。

## 9. 速度安全边界

```text
/cmd_vel/manual
/cmd_vel/nav
/cmd_vel/behavior
/web/emergency_stop + /web/control_mode
        -> /web_velocity_arbiter -> /cmd_vel -> /driver_node
```

- 仲裁器默认急停锁存，只开放当前功能对应的单一输入。
- 人工控制 350ms 超时自动归零。
- Web 最后一个控制端断开时人工模式锁停。
- Nav2 controller 和 recoveries 均重映射到 `/cmd_vel/nav`。
- iCar 行为节点均重映射到 `/cmd_vel/behavior`。
- 跨功能切换先锁停，再停止旧组件；跨容器切换还会停止旧容器以释放硬件。

## 10. 尚需实车验收

1. 在低速架空或安全场地验证直接启动 `robot_state_publisher` 的 URDF 参数和全部原子命令。
2. 验证 DWA/TEB Lifecycle 激活顺序、初始位姿和 Action 可用性。
3. 验证地图保存目录挂载及 PGM/YAML 产物。
4. 校验局部代价地图在 `odom` 与 `map` 间的前端变换。
5. 修复视觉识别的 `/dev/video0` 直接占用后再启用视觉跟随。
