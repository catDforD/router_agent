FUNCTION_BLOCK FB_Or2
VAR_INPUT
    a : BOOL;
    b : BOOL;
END_VAR
VAR_OUTPUT
    y : BOOL;
END_VAR

y := a OR b;

//#ASSERT (y = (a OR b)) : assert_y_is_a_or_b
END_FUNCTION_BLOCK
