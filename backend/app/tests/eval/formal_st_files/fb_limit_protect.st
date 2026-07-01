FUNCTION_BLOCK FB_LimitProtect
VAR_INPUT
    command : BOOL;
    limit : BOOL;
END_VAR
VAR_OUTPUT
    move : BOOL;
END_VAR

move := command AND NOT limit;

//#ASSERT (move = (command AND NOT limit)) : assert_move_respects_limit
END_FUNCTION_BLOCK
