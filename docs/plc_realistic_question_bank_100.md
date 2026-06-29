# PLC 基础真实问题题库 v7

句子再清了一轮，结构不变。

## 汇总

- 总数：100
- clarify_before_dispatch：20
- qa_direct_answer：20
- dev_then_test：10
- dev_then_test_then_formal：10
- test_only_existing_code：10
- formal_only_existing_code：10
- repair_after_test_then_test：10
- repair_after_formal_then_test_then_formal：10
- ST/CODESYS：50
- 通用 PLC：50

## clarify_before_dispatch

信息不够时先追问。
用例数：20

- `analog_scaling_alarm_clarify_generic` | `analog_scaling_alarm` | `generic_plc`
  route_hint: 模拟量缩放 条件还差，先追问。
  message:
````
压力缩放先按这个口径走：原始值 0-4095 对应 0-10 bar，8 bar 以上报高压，0.2 bar 以下报异常。断线怎么判、工程量要不要直接给 HMI 还没说清。
````

- `analog_scaling_alarm_clarify_st` | `analog_scaling_alarm` | `st_codesys`
  route_hint: 模拟量缩放 条件还差，先追问。
  message:
````
PressureBar 先按这个口径走：`RawAI` 0-4095 对应 0-10 bar，8 bar 以上报高压，0.2 bar 以下报异常。断线怎么判、工程量要不要直接给 HMI 还没定。
````

- `comm_diagnostics_clarify_generic` | `comm_diagnostics` | `generic_plc`
  route_hint: 通讯诊断 条件还差，先追问。
  message:
````
心跳逻辑先按这个口径走：3 秒收不到心跳就置通信故障，通讯恢复后还要 Reset 才能清。故障时怎么降级、自动启动许可要不要压住还没说清。
````

- `comm_diagnostics_clarify_st` | `comm_diagnostics` | `st_codesys`
  route_hint: 通讯诊断 条件还差，先追问。
  message:
````
CommFaultLatch 先按这个口径走：3 秒收不到心跳就置通信故障，通讯恢复后还要 Reset 才能清。故障时怎么降级、自动启动许可要不要压住还没定。
````

- `conveyor_sequence_clarify_generic` | `conveyor_sequence` | `generic_plc`
  route_hint: 输送线顺控 条件还差，先追问。
  message:
````
输送线顺控先按这个口径走：前段先起，后段等前段到位或延时结束再起；停机时后段先停，前段再延时停。到底有几段线、故障时下游怎么联锁还没说清。
````

- `conveyor_sequence_clarify_st` | `conveyor_sequence` | `st_codesys`
  route_hint: 输送线顺控 条件还差，先追问。
  message:
````
Conv1Cmd / Conv2Cmd 先按这个口径走：前段先起，后段等前段到位或延时结束再起；停机时后段先停，前段再延时停。到底有几段线、故障时下游怎么联锁还没定。
````

- `counter_batch_clarify_generic` | `counter_batch` | `generic_plc`
  route_hint: 批量计数 条件还差，先追问。
  message:
````
计数逻辑先按这个口径走：光电每来一个箱子记一次，满 12 个后给完成信号。完成信号要不要锁存、PushDone 之后怎么清零还没说清。
````

- `counter_batch_clarify_st` | `counter_batch` | `st_codesys`
  route_hint: 批量计数 条件还差，先追问。
  message:
````
BatchCnt 先按这个口径走：光电每来一个箱子记一次，满 12 个后给完成信号。完成信号要不要锁存、PushDone 之后怎么清零还没定。
````

- `fault_latch_reset_clarify_generic` | `fault_latch_reset` | `generic_plc`
  route_hint: 故障锁存 条件还差，先追问。
  message:
````
故障锁存先按这个口径走：`FaultIn`=TRUE 时要锁存报警并停机，Reset 只有在 `FaultIn` 恢复后才有效。StartCmd 在锁存期间要不要失效、报警清除是不是要等故障先消失还没说清。
````

- `fault_latch_reset_clarify_st` | `fault_latch_reset` | `st_codesys`
  route_hint: 故障锁存 条件还差，先追问。
  message:
````
FaultLatch 先按这个口径走：`FaultIn`=TRUE 时要锁存报警并停机，Reset 只有在 `FaultIn` 恢复后才有效。StartCmd 在锁存期间要不要失效、报警清除是不是要等故障先消失还没定。
````

- `io_mapping_hmi_clarify_generic` | `io_mapping_hmi` | `generic_plc`
  route_hint: I/O 映射 条件还差，先追问。
  message:
````
点表和 HMI 变量先按这个口径走：`DI_Start`、`DI_Stop`、`DI_Reset` 要先映射到内部命令，`MotorCmd`、`AlarmLatch` 要整理成 HMI 读的变量。现场点表还没给全，读写方向也还没定还没说清。
````

- `io_mapping_hmi_clarify_st` | `io_mapping_hmi` | `st_codesys`
  route_hint: I/O 映射 条件还差，先追问。
  message:
````
变量映射先按这个口径走：`DI_Start`、`DI_Stop`、`DI_Reset` 要先映射到内部命令，`MotorCmd`、`AlarmLatch` 要整理成 HMI 读的变量。现场点表还没给全，读写方向也还没定。
````

- `manual_auto_mode_clarify_generic` | `manual_auto_mode` | `generic_plc`
  route_hint: 手自动切换 条件还差，先追问。
  message:
````
手自动切换先按这个口径走：AutoMode 走自动逻辑，ManualMode 只允许点动。切模式时要不要强制清输出、谁先抢输出还没说清。
````

- `manual_auto_mode_clarify_st` | `manual_auto_mode` | `st_codesys`
  route_hint: 手自动切换 条件还差，先追问。
  message:
````
AutoMode / ManualMode 先按这个口径走：AutoMode 走自动逻辑，ManualMode 只允许点动。切模式时要不要强制清输出、谁先抢输出还没定。
````

- `motor_start_stop_clarify_generic` | `motor_start_stop` | `generic_plc`
  route_hint: 电机启停 条件还差，先追问。
  message:
````
电机启停先按这个口径走：`StartPB` 松开后要自保持，`StopPB`、`EStopOK`、`OverloadOK` 任一动作都要掉输出。运行反馈要不要单独点名、报警要不要锁存到复位还没说清。
````

- `motor_start_stop_clarify_st` | `motor_start_stop` | `st_codesys`
  route_hint: 电机启停 条件还差，先追问。
  message:
````
MotorCmd 先按这个口径走：`StartPB` 松开后要自保持，`StopPB`、`EStopOK`、`OverloadOK` 任一动作都要掉输出。运行反馈要不要单独点名、报警要不要锁存到复位还没定。
````

- `pump_level_control_clarify_generic` | `pump_level_control` | `generic_plc`
  route_hint: 液位泵 条件还差，先追问。
  message:
````
液位泵先按这个口径走：低液位起泵，高液位停泵；手动点动也不能绕过高液位保护。故障复位要不要等故障先恢复、手自动谁先抢输出还没说清。
````

- `pump_level_control_clarify_st` | `pump_level_control` | `st_codesys`
  route_hint: 液位泵 条件还差，先追问。
  message:
````
PumpCmd 先按这个口径走：低液位起泵，高液位停泵；手动点动也不能绕过高液位保护。故障复位要不要等故障先恢复、手自动谁先抢输出还没定。
````

- `timer_delay_clarify_generic` | `timer_delay` | `generic_plc`
  route_hint: 延时启动 条件还差，先追问。
  message:
````
延时启动先按这个口径走：`StartCmd` 触发后要等 5 秒才允许输出，`StartCmd` 一断要清零，`Fault` 要优先切断。到底用 TON、TOF 还是 TP，延时中途抖一下要不要重来还没说清。
````

- `timer_delay_clarify_st` | `timer_delay` | `st_codesys`
  route_hint: 延时启动 条件还差，先追问。
  message:
````
TON1 / MotorCmd 先按这个口径走：`StartCmd` 触发后要等 5 秒才允许输出，`StartCmd` 一断要清零，`Fault` 要优先切断。到底用 TON、TOF 还是 TP，延时中途抖一下要不要重来还没定。
````


## qa_direct_answer

能直接答就直接答。
用例数：20

- `analog_scaling_alarm_qa_generic` | `analog_scaling_alarm` | `generic_plc`
  route_hint: 模拟量缩放 偏判断，直接答。
  message:
````
模拟量越界或断线时，为什么不能只按原始值线性换算后继续给 HMI 看？
````

- `analog_scaling_alarm_qa_st` | `analog_scaling_alarm` | `st_codesys`
  route_hint: 模拟量缩放 偏判断，直接答。
  message:
````
把 `RawAI` 转成 `PressureBar` 之前，为什么还得单独处理断线和越界？
````

- `comm_diagnostics_qa_generic` | `comm_diagnostics` | `generic_plc`
  route_hint: 通讯诊断 偏判断，直接答。
  message:
````
心跳已经恢复了，为什么很多程序还是要再等一个复位才把故障放掉？
````

- `comm_diagnostics_qa_st` | `comm_diagnostics` | `st_codesys`
  route_hint: 通讯诊断 偏判断，直接答。
  message:
````
只盯着 `CommOK` 的话，为什么还要另外做超时和锁存？
````

- `conveyor_sequence_qa_generic` | `conveyor_sequence` | `generic_plc`
  route_hint: 输送线顺控 偏判断，直接答。
  message:
````
输送线为什么通常要分段起停，而不是一起拉起来？
````

- `conveyor_sequence_qa_st` | `conveyor_sequence` | `st_codesys`
  route_hint: 输送线顺控 偏判断，直接答。
  message:
````
做成 ST 以后，为什么不能把所有段一起置位？
````

- `counter_batch_qa_generic` | `counter_batch` | `generic_plc`
  route_hint: 批量计数 偏判断，直接答。
  message:
````
计数为什么一般要按上升沿来记？
````

- `counter_batch_qa_st` | `counter_batch` | `st_codesys`
  route_hint: 批量计数 偏判断，直接答。
  message:
````
在 ST 里，为什么 `R_TRIG` 比直接看 `PhotoEye` 更稳？
````

- `fault_latch_reset_qa_generic` | `fault_latch_reset` | `generic_plc`
  route_hint: 故障锁存 偏判断，直接答。
  message:
````
故障已经消了，为什么锁存还是要留给 Reset？
````

- `fault_latch_reset_qa_st` | `fault_latch_reset` | `st_codesys`
  route_hint: 故障锁存 偏判断，直接答。
  message:
````
只看 `ResetPB` 的话，为什么 `FaultLatch` 容易出问题？
````

- `io_mapping_hmi_qa_generic` | `io_mapping_hmi` | `generic_plc`
  route_hint: I/O 映射 偏判断，直接答。
  message:
````
点表和 HMI 变量为什么最好先理顺，再写控制逻辑？
````

- `io_mapping_hmi_qa_st` | `io_mapping_hmi` | `st_codesys`
  route_hint: I/O 映射 偏判断，直接答。
  message:
````
变量映射先做出来，为什么比直接写控制逻辑更省事？
````

- `manual_auto_mode_qa_generic` | `manual_auto_mode` | `generic_plc`
  route_hint: 手自动切换 偏判断，直接答。
  message:
````
手动和自动都想管输出的时候，为什么要先把优先级定住？
````

- `manual_auto_mode_qa_st` | `manual_auto_mode` | `st_codesys`
  route_hint: 手自动切换 偏判断，直接答。
  message:
````
在 ST 里，`AutoMode` 和 `ManualMode` 为什么不能同时放过同一个输出？
````

- `motor_start_stop_qa_generic` | `motor_start_stop` | `generic_plc`
  route_hint: 电机启停 偏判断，直接答。
  message:
````
电机启停里，急停和过载为什么不能只看一个？
````

- `motor_start_stop_qa_st` | `motor_start_stop` | `st_codesys`
  route_hint: 电机启停 偏判断，直接答。
  message:
````
为什么 `MotorCmd` 不能只靠 `StartPB` 自己维持？
````

- `pump_level_control_qa_generic` | `pump_level_control` | `generic_plc`
  route_hint: 液位泵 偏判断，直接答。
  message:
````
水泵液位控制里，为什么启动和停止通常不会落在同一个点上？
````

- `pump_level_control_qa_st` | `pump_level_control` | `st_codesys`
  route_hint: 液位泵 偏判断，直接答。
  message:
````
低液位起泵、高液位停泵，这两个动作为什么不能写成同一个分支？
````

- `timer_delay_qa_generic` | `timer_delay` | `generic_plc`
  route_hint: 延时启动 偏判断，直接答。
  message:
````
延时启动里，TON 和 TOF 的边界为什么最容易弄混？
````

- `timer_delay_qa_st` | `timer_delay` | `st_codesys`
  route_hint: 延时启动 偏判断，直接答。
  message:
````
在 ST 里，TON 和 TOF 真正不一样的地方，通常卡在哪个边界？
````


## dev_then_test

先写代码，再跑测试。
用例数：10

- `analog_scaling_alarm_dev_test_generic` | `analog_scaling_alarm` | `generic_plc`
  route_hint: 模拟量缩放 先做代码，再测。
  message:
````
原始值映射到 0-10 bar，8 bar 以上报高压，0.2 bar 以下报异常。边界值顺手跑一遍测试。
````

- `comm_diagnostics_dev_test_generic` | `comm_diagnostics` | `generic_plc`
  route_hint: 通讯诊断 先做代码，再测。
  message:
````
3 秒超时先挂故障锁存，恢复后要手动复位才清。重点跑一下恢复场景。
````

- `conveyor_sequence_dev_test_st` | `conveyor_sequence` | `st_codesys`
  route_hint: 输送线顺控 先做代码，再测。
  message:
````
先把 `Conv1Cmd` / `Conv2Cmd` 的顺序控制写清楚，故障出现时先撤后段。把起停顺序跑一遍。
````

- `counter_batch_dev_test_st` | `counter_batch` | `st_codesys`
  route_hint: 批量计数 先做代码，再测。
  message:
````
先把 `R_TRIG` 和 `BatchCnt` 写出来，完成后再由 `PushDone` 清零。重点看抖动时会不会重复计数。
````

- `fault_latch_reset_dev_test_st` | `fault_latch_reset` | `st_codesys`
  route_hint: 故障锁存 先做代码，再测。
  message:
````
先把 `FaultLatch` 和 `MotorCmd` 的关系写清，复位条件要等 `FaultIn` 先恢复。把锁存和复位跑一遍。
````

- `io_mapping_hmi_dev_test_st` | `io_mapping_hmi` | `st_codesys`
  route_hint: I/O 映射 先做代码，再测。
  message:
````
先把 `DI_Start` / `DI_Stop` / `DI_Reset` 映射到内部命令，再把显示变量分开。确认每个按钮都只落一个点。
````

- `manual_auto_mode_dev_test_generic` | `manual_auto_mode` | `generic_plc`
  route_hint: 手自动切换 先做代码，再测。
  message:
````
先把输出归谁管说清楚，切模式时别把上一种模式的输出带过去。把切换过程跑一遍。
````

- `motor_start_stop_dev_test_st` | `motor_start_stop` | `st_codesys`
  route_hint: 电机启停 先做代码，再测。
  message:
````
先把 `MotorCmd` 的自保持搭起来，停机条件来了立刻切掉。重点看急停、过载和停止按钮。
````

- `pump_level_control_dev_test_generic` | `pump_level_control` | `generic_plc`
  route_hint: 液位泵 先做代码，再测。
  message:
````
先把起停边界写清楚，手动也别越过保护。把高低液位和手动点动都跑一遍。
````

- `timer_delay_dev_test_generic` | `timer_delay` | `generic_plc`
  route_hint: 延时启动 先做代码，再测。
  message:
````
先把 5 秒延时写出来，启动信号一断就清计时，故障来了直接停。重点看抖动和复位。
````


## dev_then_test_then_formal

先写代码，再测，再补性质。
用例数：10

- `analog_scaling_alarm_dev_test_formal_st` | `analog_scaling_alarm` | `st_codesys`
  route_hint: 模拟量缩放 先做代码，再测，再验。
  message:
````
先把 `PressureBar` 的换算写顺，再补一条性质：它必须始终在 0..10 之间。
````

- `comm_diagnostics_dev_test_formal_st` | `comm_diagnostics` | `st_codesys`
  route_hint: 通讯诊断 先做代码，再测，再验。
  message:
````
先把超时和锁存做出来，再补一条性质：3 秒没心跳时 `CommFaultLatch` 必须为真。
````

- `conveyor_sequence_dev_test_formal_generic` | `conveyor_sequence` | `generic_plc`
  route_hint: 输送线顺控 先做代码，再测，再验。
  message:
````
先落顺控，再补一条性质：后段不能在前段没准备好时先起。
````

- `counter_batch_dev_test_formal_generic` | `counter_batch` | `generic_plc`
  route_hint: 批量计数 先做代码，再测，再验。
  message:
````
先实现计数，再补一条性质：每个箱子最多只算一次。
````

- `fault_latch_reset_dev_test_formal_generic` | `fault_latch_reset` | `generic_plc`
  route_hint: 故障锁存 先做代码，再测，再验。
  message:
````
先实现锁存，再补一条性质：故障没恢复前，锁存不能自己掉。
````

- `io_mapping_hmi_dev_test_formal_generic` | `io_mapping_hmi` | `generic_plc`
  route_hint: I/O 映射 先做代码，再测，再验。
  message:
````
先把映射写好，再补一条性质：每个外部点都得落到唯一的内部变量。
````

- `manual_auto_mode_dev_test_formal_st` | `manual_auto_mode` | `st_codesys`
  route_hint: 手自动切换 先做代码，再测，再验。
  message:
````
先做模式切换，再补一条性质：`AutoCmd` 和 `JogCmd` 不能同时抢输出。
````

- `motor_start_stop_dev_test_formal_generic` | `motor_start_stop` | `generic_plc`
  route_hint: 电机启停 先做代码，再测，再验。
  message:
````
先实现自保持，再补一条性质：停机条件一到，`MotorCmd` 不能再回来。
````

- `pump_level_control_dev_test_formal_st` | `pump_level_control` | `st_codesys`
  route_hint: 液位泵 先做代码，再测，再验。
  message:
````
先做起停逻辑，再补一条性质：高液位出现时，`PumpCmd` 不能继续保持。
````

- `timer_delay_dev_test_formal_st` | `timer_delay` | `st_codesys`
  route_hint: 延时启动 先做代码，再测，再验。
  message:
````
先把 `TON1` 的边界写清，再补不变量：输出不能在计时结束前提前置位。
````


## test_only_existing_code

已有代码，先看测试。
用例数：10

- `analog_scaling_alarm_test_only_generic` | `analog_scaling_alarm` | `generic_plc`
  route_hint: 模拟量缩放 先按现有代码测。
  message:
````
代码现在是这样的：
```text
RawAI 0-4095 映射到 0-10.0 bar
8.0 bar 以上置高压
0.2 bar 以下置异常
```
上次测试里，RawAI 已经到满量程了，PressureBar 却跑到了 10.3 bar。
````

- `comm_diagnostics_test_only_generic` | `comm_diagnostics` | `generic_plc`
  route_hint: 通讯诊断 先按现有代码测。
  message:
````
代码现在是这样的：
```text
3 秒没收到心跳就置通信故障
通讯恢复后还要 Reset 才能清
故障挂着时压住自动启动许可
```
上次测试里，心跳已经回来，但故障还是一直挂着。
````

- `conveyor_sequence_test_only_st` | `conveyor_sequence` | `st_codesys`
  route_hint: 输送线顺控 先按现有代码测。
  message:
````
代码现在是这样的：
```st
IF InfeedReady THEN Conv1Cmd := TRUE; END_IF;
IF Conv1Ready THEN Conv2Cmd := TRUE; END_IF;
IF Fault THEN Conv2Cmd := FALSE; Conv1Cmd := FALSE; END_IF;
```
上次测试里，`Conv2Cmd` 先亮了，`Conv1Cmd` 还没真正稳定。
````

- `counter_batch_test_only_st` | `counter_batch` | `st_codesys`
  route_hint: 批量计数 先按现有代码测。
  message:
````
代码现在是这样的：
```st
R_TRIG1(CLK := PhotoEye);
IF R_TRIG1.Q THEN BatchCnt := BatchCnt + 1; END_IF;
IF BatchCnt >= 12 THEN BatchDone := TRUE; END_IF;
```
上次测试里，`PhotoEye` 抖了一下，`BatchCnt` 被加了两次。
````

- `fault_latch_reset_test_only_st` | `fault_latch_reset` | `st_codesys`
  route_hint: 故障锁存 先按现有代码测。
  message:
````
代码现在是这样的：
```st
IF FaultIn THEN FaultLatch := TRUE; MotorCmd := FALSE; END_IF;
IF ResetPB AND NOT FaultIn THEN FaultLatch := FALSE; END_IF;
IF FaultLatch THEN StartCmd := FALSE; END_IF;
```
上次测试里，`FaultIn` 已经没了，`FaultLatch` 却还挂着。
````

- `io_mapping_hmi_test_only_st` | `io_mapping_hmi` | `st_codesys`
  route_hint: I/O 映射 先按现有代码测。
  message:
````
代码现在是这样的：
```st
StartReq := DI_Start;
StopReq := DI_Stop;
ResetReq := DI_Reset;
AlarmView := AlarmLatch;
```
上次测试里，`DI_Reset` 按下去了，内部复位变量却没有变化。
````

- `manual_auto_mode_test_only_generic` | `manual_auto_mode` | `generic_plc`
  route_hint: 手自动切换 先按现有代码测。
  message:
````
代码现在是这样的：
```text
AutoMode 走自动逻辑
ManualMode 只允许点动
模式切换时先清输出
```
上次测试里，手动刚切回自动，输出还按上一种模式顶着。
````

- `motor_start_stop_test_only_st` | `motor_start_stop` | `st_codesys`
  route_hint: 电机启停 先按现有代码测。
  message:
````
代码现在是这样的：
```st
MotorCmd := StartPB OR MotorCmd;
IF NOT StopPB OR NOT EStopOK OR NOT OverloadOK THEN
  MotorCmd := FALSE;
END_IF;
```
上次测试里，`StopPB` 松开后，`MotorCmd` 还是偶尔亮一下。
````

- `pump_level_control_test_only_generic` | `pump_level_control` | `generic_plc`
  route_hint: 液位泵 先按现有代码测。
  message:
````
代码现在是这样的：
```text
低液位允许起泵
高液位到就停泵
故障没恢复前不准硬启动
```
上次测试里，高液位已经到位，泵却还在继续跑。
````

- `timer_delay_test_only_generic` | `timer_delay` | `generic_plc`
  route_hint: 延时启动 先按现有代码测。
  message:
````
代码现在是这样的：
```text
StartCmd 先进入延时
5 秒后才允许 MotorCmd
Fault 一来直接撤销输出
```
上次测试里，StartCmd 抖了一下以后，Timer.Q 没有完全复位。
````


## formal_only_existing_code

已有代码，先看性质。
用例数：10

- `analog_scaling_alarm_formal_only_st` | `analog_scaling_alarm` | `st_codesys`
  route_hint: 模拟量缩放 先按现有代码验。
  message:
````
代码现在是这样的：
```st
PressureBar := REAL(RawAI) * 10.0 / 4095.0;
IF PressureBar > 8.0 THEN HighAlarm := TRUE; END_IF;
IF PressureBar < 0.2 THEN SensorFail := TRUE; END_IF;
```
要验证的是：`PressureBar` 应该始终在 0..10 之间。
````

- `comm_diagnostics_formal_only_st` | `comm_diagnostics` | `st_codesys`
  route_hint: 通讯诊断 先按现有代码验。
  message:
````
要验证的是：三秒超时后 `CommFaultLatch` 必须为 TRUE。
```st
TON1(IN := NOT HeartbeatRx, PT := T#3s);
IF TON1.Q THEN CommFaultLatch := TRUE; END_IF;
IF ResetPB AND CommOK THEN CommFaultLatch := FALSE; END_IF;
```
````

- `conveyor_sequence_formal_only_generic` | `conveyor_sequence` | `generic_plc`
  route_hint: 输送线顺控 先按现有代码验。
  message:
````
代码现在是这样的：
```text
1 号先启动
2 号要等 1 号到位或延时结束
停机时先停 2 号，再延时停 1 号
```
要验证的是：后段不能在前段没准备好时先起。
````

- `counter_batch_formal_only_generic` | `counter_batch` | `generic_plc`
  route_hint: 批量计数 先按现有代码验。
  message:
````
代码现在是这样的：
```text
PhotoEye 每来一个箱子加 1
计到 12 个拉高 Done
PushDone 后计数清零
```
要验证的是：每个箱子最多只该算一次。
````

- `fault_latch_reset_formal_only_generic` | `fault_latch_reset` | `generic_plc`
  route_hint: 故障锁存 先按现有代码验。
  message:
````
代码现在是这样的：
```text
FaultIn 动作就拉起报警锁存
Reset 只有在 FaultIn 恢复后才有效
锁存期间 StartCmd 不给通过
```
要验证的是：故障没恢复前，锁存不能自己掉。
````

- `io_mapping_hmi_formal_only_generic` | `io_mapping_hmi` | `generic_plc`
  route_hint: I/O 映射 先按现有代码验。
  message:
````
代码现在是这样的：
```text
把 DI_Start、DI_Stop、DI_Reset 对到内部命令
MotorCmd 和 AlarmLatch 单独给 HMI 读取
点名和方向先别混
```
要验证的是：每个 HMI 操作都应该能落到唯一的内部变量。
````

- `manual_auto_mode_formal_only_st` | `manual_auto_mode` | `st_codesys`
  route_hint: 手自动切换 先按现有代码验。
  message:
````
代码现在是这样的：
```st
IF AutoMode THEN AutoCmd := TRUE; END_IF;
IF ManualMode THEN JogCmd := TRUE; END_IF;
IF ModeSwitched THEN AutoCmd := FALSE; JogCmd := FALSE; END_IF;
```
要验证的是：同一时刻不能让 `AutoCmd` 和 `JogCmd` 同时抢输出。
````

- `motor_start_stop_formal_only_generic` | `motor_start_stop` | `generic_plc`
  route_hint: 电机启停 先按现有代码验。
  message:
````
代码现在是这样的：
```text
StartPB 允许起动
MotorCmd 负责自保持
StopPB、EStopOK、OverloadOK 任一动作就掉输出
```
要验证的是：停机条件一到，MotorCmd 不能继续为真。
````

- `pump_level_control_formal_only_st` | `pump_level_control` | `st_codesys`
  route_hint: 液位泵 先按现有代码验。
  message:
````
代码现在是这样的：
```st
IF LowLevel THEN PumpCmd := TRUE; END_IF;
IF HighLevel THEN PumpCmd := FALSE; END_IF;
IF Fault THEN PumpCmd := FALSE; END_IF;
```
要验证的是：高液位出现时，`PumpCmd` 不能维持为真。
````

- `timer_delay_formal_only_st` | `timer_delay` | `st_codesys`
  route_hint: 延时启动 先按现有代码验。
  message:
````
要验证的是：`MotorCmd` 在 `TON1.Q` 置位前不能变成 TRUE。
```st
TON1(IN := StartCmd AND NOT Fault, PT := T#5s);
IF TON1.Q THEN MotorCmd := TRUE; END_IF;
IF NOT StartCmd OR Fault THEN MotorCmd := FALSE; END_IF;
```
````


## repair_after_test_then_test

测试失败，先修再回归。
用例数：10

- `analog_scaling_alarm_repair_test_generic` | `analog_scaling_alarm` | `generic_plc`
  route_hint: 模拟量缩放 先修测试暴露的问题。
  message:
````
代码现在是这样的：
```text
RawAI 0-4095 映射到 0-10.0 bar
8.0 bar 以上置高压
0.2 bar 以下置异常
```
上次测试里，RawAI 已经到满量程了，PressureBar 却跑到了 10.3 bar。修完后再看满量程。
````

- `comm_diagnostics_repair_test_generic` | `comm_diagnostics` | `generic_plc`
  route_hint: 通讯诊断 先修测试暴露的问题。
  message:
````
上次回归里暴露出的问题是：心跳已经回来，但故障还是一直挂着。
```text
3 秒没收到心跳就置通信故障
通讯恢复后还要 Reset 才能清
故障挂着时压住自动启动许可
```
修完再测。
````

- `conveyor_sequence_repair_test_st` | `conveyor_sequence` | `st_codesys`
  route_hint: 输送线顺控 先修测试暴露的问题。
  message:
````
代码现在是这样的：
```st
IF InfeedReady THEN Conv1Cmd := TRUE; END_IF;
IF Conv1Ready THEN Conv2Cmd := TRUE; END_IF;
IF Fault THEN Conv2Cmd := FALSE; Conv1Cmd := FALSE; END_IF;
```
上次测试里，`Conv2Cmd` 先亮了，`Conv1Cmd` 还没真正稳定。修顺后再过一次起停。
````

- `counter_batch_repair_test_st` | `counter_batch` | `st_codesys`
  route_hint: 批量计数 先修测试暴露的问题。
  message:
````
这段 ST 过不了测试：`PhotoEye` 抖了一下，`BatchCnt` 被加了两次。
```st
R_TRIG1(CLK := PhotoEye);
IF R_TRIG1.Q THEN BatchCnt := BatchCnt + 1; END_IF;
IF BatchCnt >= 12 THEN BatchDone := TRUE; END_IF;
```
改完后再跑一遍回归。
````

- `fault_latch_reset_repair_test_st` | `fault_latch_reset` | `st_codesys`
  route_hint: 故障锁存 先修测试暴露的问题。
  message:
````
代码现在是这样的：
```st
IF FaultIn THEN FaultLatch := TRUE; MotorCmd := FALSE; END_IF;
IF ResetPB AND NOT FaultIn THEN FaultLatch := FALSE; END_IF;
IF FaultLatch THEN StartCmd := FALSE; END_IF;
```
上次测试里，`FaultIn` 已经没了，`FaultLatch` 却还挂着。修完后再看锁存和复位。
````

- `io_mapping_hmi_repair_test_st` | `io_mapping_hmi` | `st_codesys`
  route_hint: I/O 映射 先修测试暴露的问题。
  message:
````
这段 ST 过不了测试：`DI_Reset` 按下去了，内部复位变量却没有变化。
```st
StartReq := DI_Start;
StopReq := DI_Stop;
ResetReq := DI_Reset;
AlarmView := AlarmLatch;
```
改完后把回归再过一遍。
````

- `manual_auto_mode_repair_test_generic` | `manual_auto_mode` | `generic_plc`
  route_hint: 手自动切换 先修测试暴露的问题。
  message:
````
代码现在是这样的：
```text
AutoMode 走自动逻辑
ManualMode 只允许点动
模式切换时先清输出
```
上次测试里，手动刚切回自动，输出还按上一种模式顶着。修完后再切一次模式。
````

- `motor_start_stop_repair_test_st` | `motor_start_stop` | `st_codesys`
  route_hint: 电机启停 先修测试暴露的问题。
  message:
````
这段 ST 过不了测试：`StopPB` 松开后，`MotorCmd` 还是偶尔亮一下。
```st
MotorCmd := StartPB OR MotorCmd;
IF NOT StopPB OR NOT EStopOK OR NOT OverloadOK THEN
  MotorCmd := FALSE;
END_IF;
```
改完后再接着回归。
````

- `pump_level_control_repair_test_generic` | `pump_level_control` | `generic_plc`
  route_hint: 液位泵 先修测试暴露的问题。
  message:
````
代码现在是这样的：
```text
低液位允许起泵
高液位到就停泵
故障没恢复前不准硬启动
```
上次测试里，高液位已经到位，泵却还在继续跑。修完后再看高液位。
````

- `timer_delay_repair_test_generic` | `timer_delay` | `generic_plc`
  route_hint: 延时启动 先修测试暴露的问题。
  message:
````
上次回归里暴露出的问题是：StartCmd 抖了一下以后，Timer.Q 没有完全复位。
```text
StartCmd 先进入延时
5 秒后才允许 MotorCmd
Fault 一来直接撤销输出
```
修完再测。
````


## repair_after_formal_then_test_then_formal

反例已出，先修再验。
用例数：10

- `analog_scaling_alarm_repair_formal_st` | `analog_scaling_alarm` | `st_codesys`
  route_hint: 模拟量缩放 先修反例，再回到测试和验证。
  message:
````
代码现在是这样的：
```st
PressureBar := REAL(RawAI) * 10.0 / 4095.0;
IF PressureBar > 8.0 THEN HighAlarm := TRUE; END_IF;
IF PressureBar < 0.2 THEN SensorFail := TRUE; END_IF;
```
反例是：原始值越界时，显示值还在正常范围里。修好后再跑测试和形式验证。
````

- `comm_diagnostics_repair_formal_st` | `comm_diagnostics` | `st_codesys`
  route_hint: 通讯诊断 先修反例，再回到测试和验证。
  message:
````
这段 ST 的反例是：心跳已恢复，`AutoStartPermit` 却先变成 TRUE。
```st
TON1(IN := NOT HeartbeatRx, PT := T#3s);
IF TON1.Q THEN CommFaultLatch := TRUE; END_IF;
IF ResetPB AND CommOK THEN CommFaultLatch := FALSE; END_IF;
```
修完后，再把测试和性质检查都过一遍。
````

- `conveyor_sequence_repair_formal_generic` | `conveyor_sequence` | `generic_plc`
  route_hint: 输送线顺控 先修反例，再回到测试和验证。
  message:
````
代码现在是这样的：
```text
1 号先启动
2 号要等 1 号到位或延时结束
停机时先停 2 号，再延时停 1 号
```
反例是：前段还没稳定，后段就提前跑了。修好后再跑测试和形式验证。
````

- `counter_batch_repair_formal_generic` | `counter_batch` | `generic_plc`
  route_hint: 批量计数 先修反例，再回到测试和验证。
  message:
````
反例已经出来了：光电一抖，Count 直接从 3 跳到 5。
```text
PhotoEye 每来一个箱子加 1
计到 12 个拉高 Done
PushDone 后计数清零
```
改完后再把测试和形式检查补回来。
````

- `fault_latch_reset_repair_formal_generic` | `fault_latch_reset` | `generic_plc`
  route_hint: 故障锁存 先修反例，再回到测试和验证。
  message:
````
代码现在是这样的：
```text
FaultIn 动作就拉起报警锁存
Reset 只有在 FaultIn 恢复后才有效
锁存期间 StartCmd 不给通过
```
反例是：FaultIn 还在，Reset 却把报警清了。修好后再跑测试和形式验证。
````

- `io_mapping_hmi_repair_formal_generic` | `io_mapping_hmi` | `generic_plc`
  route_hint: I/O 映射 先修反例，再回到测试和验证。
  message:
````
反例已经出来了：同一个按钮同时被当成了输入和显示变量。
```text
把 DI_Start、DI_Stop、DI_Reset 对到内部命令
MotorCmd 和 AlarmLatch 单独给 HMI 读取
点名和方向先别混
```
改完后再把测试和形式检查补回来。
````

- `manual_auto_mode_repair_formal_st` | `manual_auto_mode` | `st_codesys`
  route_hint: 手自动切换 先修反例，再回到测试和验证。
  message:
````
代码现在是这样的：
```st
IF AutoMode THEN AutoCmd := TRUE; END_IF;
IF ManualMode THEN JogCmd := TRUE; END_IF;
IF ModeSwitched THEN AutoCmd := FALSE; JogCmd := FALSE; END_IF;
```
反例是：自动已经回来，点动却还没真正退出。修好后再跑测试和形式验证。
````

- `motor_start_stop_repair_formal_generic` | `motor_start_stop` | `generic_plc`
  route_hint: 电机启停 先修反例，再回到测试和验证。
  message:
````
反例已经出来了：StopPB 已经断开，MotorCmd 却晚了一拍才掉。
```text
StartPB 允许起动
MotorCmd 负责自保持
StopPB、EStopOK、OverloadOK 任一动作就掉输出
```
改完后再把测试和形式检查补回来。
````

- `pump_level_control_repair_formal_st` | `pump_level_control` | `st_codesys`
  route_hint: 液位泵 先修反例，再回到测试和验证。
  message:
````
代码现在是这样的：
```st
IF LowLevel THEN PumpCmd := TRUE; END_IF;
IF HighLevel THEN PumpCmd := FALSE; END_IF;
IF Fault THEN PumpCmd := FALSE; END_IF;
```
反例是：高液位已经动作，泵还在继续跑。修好后再跑测试和形式验证。
````

- `timer_delay_repair_formal_st` | `timer_delay` | `st_codesys`
  route_hint: 延时启动 先修反例，再回到测试和验证。
  message:
````
这段 ST 的反例是：`ET` 还没到 `PT`，`MotorCmd` 就提前置位了。
```st
TON1(IN := StartCmd AND NOT Fault, PT := T#5s);
IF TON1.Q THEN MotorCmd := TRUE; END_IF;
IF NOT StartCmd OR Fault THEN MotorCmd := FALSE; END_IF;
```
修完后，再把测试和性质检查都过一遍。
````
