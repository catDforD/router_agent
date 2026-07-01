FUNCTION_BLOCK FB_NotOutput
VAR_INPUT
    x : BOOL;
END_VAR
VAR_OUTPUT
    y : BOOL;
END_VAR

y := NOT x;

//#ASSERT (y = NOT x) : assert_y_is_not_x
END_FUNCTION_BLOCK
