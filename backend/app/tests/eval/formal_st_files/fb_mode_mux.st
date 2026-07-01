FUNCTION_BLOCK FB_ModeMux
VAR_INPUT
    autoMode : BOOL;
    manualCmd : BOOL;
    autoCmd : BOOL;
END_VAR
VAR_OUTPUT
    outputCmd : BOOL;
END_VAR

outputCmd := (autoMode AND autoCmd) OR ((NOT autoMode) AND manualCmd);

//#ASSERT (outputCmd = ((autoMode AND autoCmd) OR ((NOT autoMode) AND manualCmd))) : assert_mode_selects_command
END_FUNCTION_BLOCK
