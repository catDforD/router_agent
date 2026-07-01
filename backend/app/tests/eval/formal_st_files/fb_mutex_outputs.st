FUNCTION_BLOCK FB_MutexOutputs
VAR_INPUT
    fwd : BOOL;
    rev : BOOL;
END_VAR
VAR_OUTPUT
    forward : BOOL;
    reverse : BOOL;
END_VAR

forward := fwd AND NOT rev;
reverse := rev AND NOT fwd;

//#ASSERT (NOT (forward AND reverse)) : assert_forward_reverse_mutex
END_FUNCTION_BLOCK
