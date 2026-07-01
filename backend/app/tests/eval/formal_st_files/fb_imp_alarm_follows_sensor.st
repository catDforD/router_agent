FUNCTION_BLOCK FB_Imp
VAR_INPUT
    sensor : BOOL;
END_VAR
VAR_OUTPUT
    alarm : BOOL;
END_VAR

alarm := sensor;

//#ASSERT (alarm = sensor) : assert_alarm_follows_sensor
END_FUNCTION_BLOCK
