# PLC 基础真实问题题库 v9

本版保留澄清和 QA 样例，保留四类执行 workflow 样例，并删除修复类路由。机器可读题库文件为 `backend/app/tests/eval/plc_realistic_question_bank.json`。

## 汇总

- 总数：80
- clarify_before_dispatch：20
- qa_direct_answer：20
- dev_then_test：10
- dev_then_test_then_formal：10
- test_only_existing_code：10
- formal_only_existing_code：10
- generic_plc：20
- st_codesys：60

## 元数据说明

- `benchmark_id` / `benchmark_st_path`：执行类样例绑定的 ST 来源；开发和测试类来自 raw benchmark，formal-only 可来自 proof-friendly fixture。
- `validation_focus`：测试或形式化验证关注点。

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

新开发需求：先写代码，再跑测试。
用例数：10

- `find_min_max_values_dev_test` | `find_min_max_values` | `st_codesys`
  route_hint: 最小最大值功能块 先实现，再测边界。
  benchmark: `212_FindMinMaxValues`
  source: `data/raw-data/benchmark_raw/212_FindMinMaxValues/plc_st_files/212_FindMinMaxValues.st`
  validation_focus: 覆盖 10 个 DINT 输入的负数、重复值、全相等、首尾元素为最值等场景，最值索引取第一次出现的位置。
  message:
````
请帮我写一个 `FindMinMaxValues` 功能块，输入固定 10 个 DINT，输出最小值、最大值以及它们的位置索引。写完后把负数、重复值、全相等、首尾元素为最值这些边界都跑一遍测试。
````

- `day_of_year_calculator_dev_test` | `day_of_year_calculator` | `st_codesys`
  route_hint: 年内日序功能块 先实现，再测闰年。
  benchmark: `214_DayOfYearCalculator`
  source: `data/raw-data/benchmark_raw/214_DayOfYearCalculator/plc_st_files/214_DayOfYearCalculator.st`
  validation_focus: 覆盖平年、闰年、2 月 29 日、年初年末，以及非法月日输入的错误处理。
  message:
````
请实现一个 `FB_CalculateDayOfYear` 功能块，输入年、月、日，输出当天是这一年的第几天，并给非法日期置错误状态。完成后重点测试平年/闰年、2 月 29 日、1 月 1 日和 12 月 31 日。
````

- `select_sort_dev_test` | `select_sort` | `st_codesys`
  route_hint: 选择排序功能块 先实现，再测顺序。
  benchmark: `215_SelectSort`
  source: `data/raw-data/benchmark_raw/215_SelectSort/plc_st_files/215_SelectSort.st`
  validation_focus: 覆盖升序、降序、重复值、负数，以及 execute 为 FALSE 时不改变输出的情况。
  message:
````
请写一个 `SelectSort` 功能块，对 10 个 DINT 做升序排序，只有 `Execute` 触发时才更新结果。实现后用已经有序、完全逆序、带重复值和带负数的数组各跑一次测试。
````

- `random_number_generator_dev_test` | `random_number_generator` | `st_codesys`
  route_hint: 随机数功能块 先实现，再测范围。
  benchmark: `216_RandomNumber`
  source: `data/raw-data/benchmark_raw/216_RandomNumber/plc_st_files/216_RandomNumber.st`
  validation_focus: 覆盖最小最大范围、min=max、seed 重复时结果可复现，以及结果不越界。
  message:
````
请实现一个简单的 `RandomNumberGenerator` 功能块，输入最小值、最大值和 seed，输出范围内的整数；同一个 seed 要能复现同样结果。实现后测试 min=max、正常范围、多组 seed 和结果不越界。
````

- `floating_average_dev_test` | `floating_average` | `st_codesys`
  route_hint: 滑动平均功能块 先实现，再测窗口。
  benchmark: `220_FloatingAverage`
  source: `data/raw-data/benchmark_raw/220_FloatingAverage/plc_st_files/220_FloatingAverage.st`
  validation_focus: 覆盖窗口未填满、窗口填满后滑动、Reset 清空、Trigger 控制采样，以及 WindowSize 边界。
  message:
````
请写一个 `FloatingAverage` 功能块，按触发信号采样 REAL 值并计算滑动平均，支持窗口大小和 Reset。写完后重点测试窗口还没填满、填满后滚动、Reset 清空以及 Trigger 没来时不采样。
````

- `lights_control_dev_test` | `lights_control` | `st_codesys`
  route_hint: 三灯控制功能块 先实现，再测模式切换。
  benchmark: `223_LightsControl`
  source: `data/raw-data/benchmark_raw/223_LightsControl/plc_st_files/223_LightsControl.st`
  validation_focus: 覆盖自动轮转、手动按钮组合、无按钮时全灭，以及任意时刻红黄绿输出互斥。
  message:
````
请实现一个三色灯 `LightsControl` 功能块：自动模式下绿、红、黄按定时顺序轮转，手动按钮可以单独点亮对应灯。完成后跑一下自动轮转、按钮组合、无按钮全灭和三灯互斥测试。
````

- `linearize_value_dev_test` | `linearize_value` | `st_codesys`
  route_hint: 分段线性化功能块 先实现，再测断点。
  benchmark: `230_FB_LinearizeValue`
  source: `data/raw-data/benchmark_raw/230_FB_LinearizeValue/plc_st_files/230_FB_LinearizeValue.st`
  validation_focus: 覆盖 1200、3600、4800、7000 四个断点及断点外错误状态，分段内输出应单调。
  message:
````
请实现 `FB_LinearizeValue`，把模拟输入按 1200-3600、3600-4800、4800-7000 三段线性换算，超出范围时给错误状态。测试重点放在每个断点、断点外和分段内单调性。
````

- `fifo_queue_dev_test` | `fifo_queue` | `st_codesys`
  route_hint: FIFO 队列功能块 先实现，再测满空。
  benchmark: `236_FIFOQueue`
  source: `data/raw-data/benchmark_raw/236_FIFOQueue/plc_st_files/236_FIFOQueue.st`
  validation_focus: 覆盖入队、出队、满队列拒绝、空队列出队、Reset/Clear 后状态清空。
  message:
````
请写一个固定长度 FIFO 功能块，支持 enqueue、dequeue、reset 和 clear，输出当前元素和队列状态。实现后把连续入队、连续出队、满队列、空队列和清空后的状态都测一下。
````

- `bit_edge_detector_dev_test` | `bit_edge_detector` | `st_codesys`
  route_hint: 位边沿检测功能块 先实现，再测跳变。
  benchmark: `239_BitEdgeDetector`
  source: `data/raw-data/benchmark_raw/239_BitEdgeDetector/plc_st_files/239_BitEdgeDetector.st`
  validation_focus: 覆盖 BOOL 序列的上升沿、下降沿、保持不变、多次跳变，边沿输出只保持一个扫描周期。
  message:
````
请实现一个 `BitEdgeDetector`，输入 BOOL 当前值，输出上升沿和下降沿脉冲。完成后用连续 TRUE、连续 FALSE、0-1-0 跳变和多次抖动序列做测试，确认边沿只出一个扫描周期。
````

- `pulse_generator_dev_test` | `pulse_generator` | `st_codesys`
  route_hint: 脉冲发生器功能块 先实现，再测频率占空。
  benchmark: `252_PulseGenerator`
  source: `data/raw-data/benchmark_raw/252_PulseGenerator/plc_st_files/252_PulseGenerator.st`
  validation_focus: 覆盖频率为 0、正常频率、不同高低电平比例，以及 remainingTime 不出现负值。
  message:
````
请实现 `PulseGenerator`，按输入频率和高低电平比例生成周期脉冲，同时给出当前状态剩余时间。写完后测试频率为 0、正常频率、不同占空比例和剩余时间不为负。
````


## dev_then_test_then_formal

新开发需求：代码、测试、形式化性质一起完成。
用例数：10

- `hex_digit_analyzer_dev_test_formal` | `hex_digit_analyzer` | `st_codesys`
  route_hint: 十六进制位分析 代码、测试、性质一起做。
  benchmark: `218_HexDigitAnalyzer`
  source: `data/raw-data/benchmark_raw/218_HexDigitAnalyzer/plc_st_files/218_HexDigitAnalyzer.st`
  validation_focus: 性质：输入 0..15 时只允许一个合法 digit 输出为真，输入越界时必须进入错误状态。
  message:
````
请实现 `FB_HexDigitAnalyzer`，识别 0..15 的十六进制 digit 类型并处理越界输入。除了常规测试，再补一条形式化性质：合法输入时分类输出不能互相冲突，非法输入时必须置错误。
````

- `truck_garage_dev_test_formal` | `truck_garage` | `st_codesys`
  route_hint: 车库管理 代码、测试、性质一起做。
  benchmark: `224_TruckGarage`
  source: `data/raw-data/benchmark_raw/224_TruckGarage/plc_st_files/224_TruckGarage.st`
  validation_focus: 性质：同一个车位不能同时分配给两辆车，出库后该车位必须清空。
  message:
````
请实现一个三行五列的 `TruckGarage` 功能块，入库时找空位写入车号，出库时返回车位和车号并清空槽位。测试入库、出库、满库，再验证性质：任何时刻一个车位只能属于一辆车。
````

- `merge_bytes_dev_test_formal` | `merge_bytes` | `st_codesys`
  route_hint: 字节合并 代码、测试、性质一起做。
  benchmark: `225_MergeBytes`
  source: `data/raw-data/benchmark_raw/225_MergeBytes/plc_st_files/225_MergeBytes.st`
  validation_focus: 性质：输出 DWORD 的每个字节必须只来自对应输入字节，不允许字节顺序错位。
  message:
````
请实现 `FB_MergeBytes`，把字节数组按指定顺序合成 WORD/DWORD 输出。常规测试覆盖 0、255 和混合字节，再补性质验证：每个输出字节都必须来自唯一的输入位置。
````

- `extract_substring_dev_test_formal` | `extract_substring` | `st_codesys`
  route_hint: 字符串截取 代码、测试、性质一起做。
  benchmark: `226_ExtractSubstring`
  source: `data/raw-data/benchmark_raw/226_ExtractSubstring/plc_st_files/226_ExtractSubstring.st`
  validation_focus: 性质：截取范围不得越过源字符串边界，空结果时长度必须为 0。
  message:
````
请实现一个字符串/字符数组截取功能块，按起始位置和长度输出子串。测试正常截取、起点在边界、长度超出和空串，再验证性质：任何输入下都不能访问源字符串范围外的位置。
````

- `shift_sequence_dev_test_formal` | `shift_sequence` | `st_codesys`
  route_hint: 移位序列 代码、测试、性质一起做。
  benchmark: `232_ShiftSequence`
  source: `data/raw-data/benchmark_raw/232_ShiftSequence/plc_st_files/232_ShiftSequence.st`
  validation_focus: 性质：移位后有效元素数量不超过缓冲区容量，Clear 后所有槽位回到初始值。
  message:
````
请实现 `ShiftSequence`，支持左移、右移、指定范围移位和 Clear。完成后跑左右移、边界范围和 Clear 测试，再验证性质：缓冲区索引不越界，Clear 后状态完全清空。
````

- `color_light_control_dev_test_formal` | `color_light_control` | `st_codesys`
  route_hint: 颜色灯控制 代码、测试、性质一起做。
  benchmark: `237_FB_ColorLightControl`
  source: `data/raw-data/benchmark_raw/237_FB_ColorLightControl/plc_st_files/237_FB_ColorLightControl.st`
  validation_focus: 性质：任一控制码下红、黄、绿互斥组合必须符合定义，非法控制码必须输出安全状态。
  message:
````
请实现 `FB_ColorLightControl`，根据控制码输出红黄绿状态，非法控制码进入安全输出。测试每个控制码，再补形式化性质：输出组合必须属于允许集合，不能出现未定义灯态。
````

- `ring_queue_multi_item_dev_test_formal` | `ring_queue_multi_item` | `st_codesys`
  route_hint: 环形队列 代码、测试、性质一起做。
  benchmark: `241_RingQueueMultiItem`
  source: `data/raw-data/benchmark_raw/241_RingQueueMultiItem/plc_st_files/241_RingQueueMultiItem.st`
  validation_focus: 性质：读写指针始终落在队列容量范围内，队列满时不能覆盖未弹出的元素。
  message:
````
请实现一个支持批量 push/pop 的环形队列功能块，能按 itemIndex 和 itemLen 处理多元素。测试回绕、满队列、空队列和 Reset，再验证性质：读写指针不越界，满队列不覆盖有效数据。
````

- `parity_check_dev_test_formal` | `parity_check` | `st_codesys`
  route_hint: 奇偶校验 代码、测试、性质一起做。
  benchmark: `248_ParityCheck`
  source: `data/raw-data/benchmark_raw/248_ParityCheck/plc_st_files/248_ParityCheck.st`
  validation_focus: 性质：翻转输入中的任意一位会翻转奇偶校验结果。
  message:
````
请实现 `ParityCheck`，输入数值和奇偶选择，输出校验是否通过。测试 0、单 bit、多 bit、最大值，再验证性质：任意翻转一位时，奇偶结果必须发生翻转。
````

- `string_extractor_dev_test_formal` | `string_extractor` | `st_codesys`
  route_hint: 字符串提取 代码、测试、性质一起做。
  benchmark: `250_StringExtractor`
  source: `data/raw-data/benchmark_raw/250_StringExtractor/plc_st_files/250_StringExtractor.st`
  validation_focus: 性质：输出长度不超过目标缓冲区，includeBeforeAfter 只影响边界字符是否保留。
  message:
````
请实现 `GetString`，从字符数组中按前后分隔符提取片段，并支持是否包含分隔符。测试找得到、找不到、起始位置在末尾、包含分隔符，再验证性质：输出永远不超过目标缓冲区。
````

- `split_number_dev_test_formal` | `split_number` | `st_codesys`
  route_hint: 数字拆分 代码、测试、性质一起做。
  benchmark: `255_SplitNumber`
  source: `data/raw-data/benchmark_raw/255_SplitNumber/plc_st_files/255_SplitNumber.st`
  validation_focus: 性质：拆出的高低位重新组合后必须等于原始输入。
  message:
````
请实现 `FB_SplitNumber`，把输入整数拆成高低字节/字并输出。测试 0、边界值、随机值和最大值，再补性质验证：把拆分结果重新组合后必须得到原始输入。
````


## test_only_existing_code

已有 ST：只做测试评估，不改代码。
用例数：10

- `multi_pump_control_test_only_existing_code` | `multi_pump_control` | `st_codesys`
  route_hint: 多泵控制 只测现有 ST。
  benchmark: `213_MultiPumpCtrl`
  source: `data/raw-data/benchmark_raw/213_MultiPumpCtrl/plc_st_files/213_MultiPumpCtrl.st`
  validation_focus: 验证自动模式最多 3 台泵运行、按优先级选择且 stop 优先清零。
  message:
````
只对现有 `MultiPumpCtrl` 做测试：自动模式应按优先级最多启动 3 台泵，手动模式只跟随 selections，stop 后所有运行命令清零。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `analog_batch_processing_test_only_existing_code` | `analog_batch_processing` | `st_codesys`
  route_hint: 模拟量批处理 只测现有 ST。
  benchmark: `217_AnalogBatchProcessing`
  source: `data/raw-data/benchmark_raw/217_AnalogBatchProcessing/plc_st_files/217_AnalogBatchProcessing.st`
  validation_focus: 验证禁用通道不报警、启用通道按各自上下限报警，Count 边界不越界。
  message:
````
只测现有 `AnalogBatchProcessing`：启用通道才参与上下限判断，双极性和测量模式不要互相影响，通道数量边界要跑到。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `bottle_processing_test_only_existing_code` | `bottle_processing` | `st_codesys`
  route_hint: 瓶装线顺控 只测现有 ST。
  benchmark: `221_BottleProcessing`
  source: `data/raw-data/benchmark_raw/221_BottleProcessing/plc_st_files/221_BottleProcessing.st`
  validation_focus: 验证每个确认按钮只在对应步骤有效，完成取走后所有步骤和输出复位。
  message:
````
只对现有 `FB_BottleProcessing` 做测试评估：一瓶进入后必须按清洗、灌装、旋盖、包装、完成的顺序走，未确认前不能跳到下一步。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `material_mixing_test_only_existing_code` | `material_mixing` | `st_codesys`
  route_hint: 物料混合 只测现有 ST。
  benchmark: `222_MaterialMixing`
  source: `data/raw-data/benchmark_raw/222_MaterialMixing/plc_st_files/222_MaterialMixing.st`
  validation_focus: 验证急停最高优先级、液位条件未满足时搅拌电机不启动、自动流程按阶段输出。
  message:
````
只测现有 `MaterialMixing`：急停优先，手自动模式下阀门和搅拌电机的输出要按工艺阶段走，液位未到不能启动混合。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `temperature_control_test_only_existing_code` | `temperature_control` | `st_codesys`
  route_hint: 温控 只测现有 ST。
  benchmark: `227_TempCtrl`
  source: `data/raw-data/benchmark_raw/227_TempCtrl/plc_st_files/227_TempCtrl.st`
  validation_focus: 验证传感器 0..100 范围、低温加热、高温停止、越界关断和过热保护锁定。
  message:
````
只对现有 `TempCtrl` 做测试：温度低于设定值才允许加热，传感器越界要关加热，过热保护定时结束前不能恢复输出。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `flexible_production_test_only_existing_code` | `flexible_production` | `st_codesys`
  route_hint: 柔性产线 只测现有 ST。
  benchmark: `228_FlexibleProduction`
  source: `data/raw-data/benchmark_raw/228_FlexibleProduction/plc_st_files/228_FlexibleProduction.st`
  validation_focus: 验证站点顺序推进、工位完成信号门控、临时数组索引不越界。
  message:
````
只测现有 `FlexibleProduction`：五个传感器和各工位完成信号要按顺序推进，临时数组写入不能越界，未到位不能提前启动下一站。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `recipe_manager_test_only_existing_code` | `recipe_manager` | `st_codesys`
  route_hint: 配方管理 只测现有 ST。
  benchmark: `229_RecipeManager`
  source: `data/raw-data/benchmark_raw/229_RecipeManager/plc_st_files/229_RecipeManager.st`
  validation_focus: 验证 recipeID 唯一、满表拒绝新增、删除后槽位释放、查询不存在时返回错误。
  message:
````
只对现有 `FB_RecipeManager` 做测试：新增、删除、修改、查询配方都要按 recipeID 定位，满表和重复 ID 要有明确状态。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `mechanical_arm_control_test_only_existing_code` | `mechanical_arm_control` | `st_codesys`
  route_hint: 机械臂控制 只测现有 ST。
  benchmark: `238_MechanicalArmControl`
  source: `data/raw-data/benchmark_raw/238_MechanicalArmControl/plc_st_files/238_MechanicalArmControl.st`
  validation_focus: 验证急停优先、限位保护、模式互斥，以及抓取/释放动作不同时有效。
  message:
````
只测现有 `MechanicalArm`：手动、单步、单周期、连续模式的动作不能互相抢输出，急停和限位必须立即压住对应动作。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `production_line_control_test_only_existing_code` | `production_line_control` | `st_codesys`
  route_hint: 生产线控制 只测现有 ST。
  benchmark: `249_ProductionLineControl`
  source: `data/raw-data/benchmark_raw/249_ProductionLineControl/plc_st_files/249_ProductionLineControl.st`
  validation_focus: 验证正反转互斥、自动工位推进、完成灯复位和模式切换清输出。
  message:
````
只对现有 `FB_ProductionLineControl` 做测试：自动模式按 A/B/C 工位传感器和按钮推进，手动模式正反转互斥，切模式时不要保留旧输出。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````

- `alarm_process_test_only_existing_code` | `alarm_process` | `st_codesys`
  route_hint: 报警处理 只测现有 ST。
  benchmark: `253_AlarmProcess`
  source: `data/raw-data/benchmark_raw/253_AlarmProcess/plc_st_files/253_AlarmProcess.st`
  validation_focus: 验证高低限报警迟滞、非法上下限、迟滞窗口过大和 status 码。
  message:
````
只测现有 `AlarmProcess`：高低报警要带迟滞，loLevel >= hiLevel 或迟滞窗口过大时必须置错误状态。 请只做测试评估，先不要改代码、不要修复，也不要做形式化验证；如果失败，只列失败用例和可能原因。
````


## formal_only_existing_code

已有 ST：只做形式化性质验证，不改代码。本组使用小型 proof-friendly fixture，避免复杂工程 ST 的解析和证明能力污染 route 评估。
用例数：10

- `example_y_equals_x_formal_only_existing_code` | `formal_bool_identity` | `st_codesys`
  route_hint: 布尔直通 只验证现有 ST 断言。
  benchmark: `formal_fixture_Example`
  source: `backend/app/tests/eval/formal_st_files/example_y_equals_x.st`
  validation_focus: 验证内嵌断言 assert_y_equals_x：输出 y 始终等于输入 x。
  message:
````
请只对现有 `Example` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (y = x) : assert_y_equals_x`，请按 assertion property 验证 `y` 始终等于 `x`。
````

- `imp_alarm_follows_sensor_formal_only_existing_code` | `formal_alarm_implication` | `st_codesys`
  route_hint: 报警直通 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_Imp`
  source: `backend/app/tests/eval/formal_st_files/fb_imp_alarm_follows_sensor.st`
  validation_focus: 验证内嵌断言 assert_alarm_follows_sensor：alarm 始终等于 sensor。
  message:
````
请只对现有 `FB_Imp` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (alarm = sensor) : assert_alarm_follows_sensor`，请按 assertion property 验证报警输出跟随传感器输入。
````

- `not_output_formal_only_existing_code` | `formal_bool_not` | `st_codesys`
  route_hint: 布尔取反 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_NotOutput`
  source: `backend/app/tests/eval/formal_st_files/fb_not_output.st`
  validation_focus: 验证内嵌断言 assert_y_is_not_x：输出 y 始终等于 NOT x。
  message:
````
请只对现有 `FB_NotOutput` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (y = NOT x) : assert_y_is_not_x`，请按 assertion property 验证 `y` 始终为 `x` 的取反。
````

- `and2_output_formal_only_existing_code` | `formal_bool_and` | `st_codesys`
  route_hint: 布尔与 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_And2`
  source: `backend/app/tests/eval/formal_st_files/fb_and2_output.st`
  validation_focus: 验证内嵌断言 assert_y_is_a_and_b：输出 y 始终等于 a AND b。
  message:
````
请只对现有 `FB_And2` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (y = (a AND b)) : assert_y_is_a_and_b`，请按 assertion property 验证 `y` 始终等于 `a AND b`。
````

- `or2_output_formal_only_existing_code` | `formal_bool_or` | `st_codesys`
  route_hint: 布尔或 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_Or2`
  source: `backend/app/tests/eval/formal_st_files/fb_or2_output.st`
  validation_focus: 验证内嵌断言 assert_y_is_a_or_b：输出 y 始终等于 a OR b。
  message:
````
请只对现有 `FB_Or2` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (y = (a OR b)) : assert_y_is_a_or_b`，请按 assertion property 验证 `y` 始终等于 `a OR b`。
````

- `stop_dominates_formal_only_existing_code` | `formal_stop_interlock` | `st_codesys`
  route_hint: 停止优先 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_StopDominates`
  source: `backend/app/tests/eval/formal_st_files/fb_stop_dominates.st`
  validation_focus: 验证内嵌断言 assert_motor_respects_stop：motor 始终等于 start AND NOT stop。
  message:
````
请只对现有 `FB_StopDominates` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (motor = (start AND NOT stop)) : assert_motor_respects_stop`，请按 assertion property 验证 motor 始终等于 `start AND NOT stop`。
````

- `fault_dominates_formal_only_existing_code` | `formal_fault_interlock` | `st_codesys`
  route_hint: 故障优先 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_FaultDominates`
  source: `backend/app/tests/eval/formal_st_files/fb_fault_dominates.st`
  validation_focus: 验证内嵌断言 assert_run_respects_fault：run 始终等于 enable AND NOT fault。
  message:
````
请只对现有 `FB_FaultDominates` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (run = (enable AND NOT fault)) : assert_run_respects_fault`，请按 assertion property 验证 run 始终等于 `enable AND NOT fault`。
````

- `limit_protect_formal_only_existing_code` | `formal_limit_interlock` | `st_codesys`
  route_hint: 限位保护 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_LimitProtect`
  source: `backend/app/tests/eval/formal_st_files/fb_limit_protect.st`
  validation_focus: 验证内嵌断言 assert_move_respects_limit：move 始终等于 command AND NOT limit。
  message:
````
请只对现有 `FB_LimitProtect` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (move = (command AND NOT limit)) : assert_move_respects_limit`，请按 assertion property 验证 move 始终等于 `command AND NOT limit`。
````

- `mode_mux_formal_only_existing_code` | `formal_mode_mux` | `st_codesys`
  route_hint: 模式选择 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_ModeMux`
  source: `backend/app/tests/eval/formal_st_files/fb_mode_mux.st`
  validation_focus: 验证内嵌断言 assert_mode_selects_command：模式选择输出来自对应命令。
  message:
````
请只对现有 `FB_ModeMux` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT ... : assert_mode_selects_command`，请按 assertion property 验证自动模式选 `autoCmd`，非自动模式选 `manualCmd`。
````

- `mutex_outputs_formal_only_existing_code` | `formal_output_mutex` | `st_codesys`
  route_hint: 输出互斥 只验证现有 ST 断言。
  benchmark: `formal_fixture_FB_MutexOutputs`
  source: `backend/app/tests/eval/formal_st_files/fb_mutex_outputs.st`
  validation_focus: 验证内嵌断言 assert_forward_reverse_mutex：forward 与 reverse 永不同时为 TRUE。
  message:
````
请只对现有 `FB_MutexOutputs` ST 做形式化验证，不要生成新代码，也不要修复。代码里已经有 `//#ASSERT (NOT (forward AND reverse)) : assert_forward_reverse_mutex`，请按 assertion property 验证 forward 和 reverse 不能同时为 TRUE。
````
