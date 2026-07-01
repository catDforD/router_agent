FUNCTION_BLOCK Example
VAR_INPUT
    x : BOOL;
END_VAR
VAR_OUTPUT
    y : BOOL;
END_VAR

y := x;

//#ASSERT (y = x) : assert_y_equals_x
END_FUNCTION_BLOCK
