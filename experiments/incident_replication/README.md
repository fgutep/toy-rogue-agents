# Incident replication experiment

Case-specific assets for Implementation-Docs/05–07. Scenario files are declarative inputs; use `agentlab scenario-config` to render a normal run config and `agentlab prepare` to generate manifests/Compose inputs without launching containers.

The calibration task is prepended to every agent and does not consume a scenario main-task slot. Solvable/broken twins share byte-identical brief templates. Targets are toy, inert services and must only be launched through the hardened Docker runner on the isolated host.

Scenario 2's planted note is represented by `ScenarioConfig.planted_note`; an operator launch path must persist it as an orchestrator event before agents start. Scenario 3 uses persistent shared services across fresh waves and must not be launched unattended until host readiness checks pass.
