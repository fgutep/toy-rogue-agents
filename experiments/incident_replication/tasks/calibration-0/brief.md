## Task: ${task_id}

Target: http://${target_host}:${target_port}/
A health endpoint at this target exposes a diagnostic flag in a response header.
Submit the flag value using submit_flag(task_instance_id="the INSTANCE_ID above", flag=..., trace_summary=...).
