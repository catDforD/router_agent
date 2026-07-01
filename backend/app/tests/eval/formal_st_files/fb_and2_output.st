FUNCTION_BLOCK FB_And2
VAR_INPUT
    a : BOOL;
    b : BOOL;
END_VAR
VAR_OUTPUT
    y : BOOL;
END_VAR

y := a AND b;

//#ASSERT (y = (a AND b)) : assert_y_is_a_and_b
END_FUNCTION_BLOCK
