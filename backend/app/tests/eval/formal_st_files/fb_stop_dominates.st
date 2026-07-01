FUNCTION_BLOCK FB_StopDominates
VAR_INPUT
    start : BOOL;
    stop : BOOL;
END_VAR
VAR_OUTPUT
    motor : BOOL;
END_VAR

motor := start AND NOT stop;

//#ASSERT (motor = (start AND NOT stop)) : assert_motor_respects_stop
END_FUNCTION_BLOCK
