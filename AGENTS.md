# DLB experiments collaboration guide

## Communication

- Prefer Chinese for explanations, reviews, and handoff notes. Keep code identifiers and commands in their original language.
- Lead with the conclusion, then give the supporting file, command, or observed output.
- Separate confirmed defects, risks that need runtime verification, and ordinary "not run yet" states.
- If a scientific assumption, active experiment, scheduler resource, or target dataset is unclear, state the uncertainty instead of silently choosing one.

## Working preferences

- Start with a read-only inspection. Do not change the established workflow merely to make a review easier.
- When compute cores are unavailable, use only lightweight checks such as static inspection, `py_compile`, CLI `--help`, `bash -n`, dry-runs, checksum verification, and temporary-directory unit tests.
- Do not launch CoLM, OpenBench, LSF, Slurm, MPI, or other expensive work unless explicitly requested.
- Preserve existing files and user changes. Before writing, inspect Git status and keep edits small and directly tied to the request.
- Prefer dry-run, preflight, atomic replacement, backups, checksums, and explicit `--apply`, `--overwrite`, or `--submit` gates.
- Never treat a generated configuration as validated scientific output. Report exactly what was and was not exercised.
- For a review, give severity, evidence, likely impact, and a minimal verification or repair path. Do not automatically fix findings unless a fix was requested.
- Keep durable documentation near the repository so later work can reuse the same assumptions and safety boundaries.

## Stable workflow model

The managed DLB workflow is:

1. `dlb_01_create_experiment.py` creates an experiment directory without creating cases or submitting jobs.
2. `dlb_02_generate_namelists.py` generates CoLM namelists and supports guarded regeneration.
3. `dlb_03_generate_jobs.py` generates LSF or Slurm scripts; its submission helper defaults to dry-run.
4. `dlb_04_preflight.py` performs read-only input and stage checks.
5. `dlb_05_restart_handoff.py` prepares D_INIT and E0-E7 restart handoff; writes require `--apply`.
6. `dlb_06_stage_status.py` reports SPINUP, D_INIT, handoff, and experiment status.
7. `dlb_07_refresh_experiment.py` refreshes manifests, artifacts, checksums, and validation reports.
8. `dlb_08_generate_openbench.py` generates or safely replaces OpenBench configuration.

Treat basin-mask and dam-analysis scripts as supporting preprocessing/postprocessing tools, not as implicit steps that may be run during a workflow review.

## Verification levels

- Level 1, always safe: repository inventory, Git status, syntax compilation, CLI help, manifest inspection, `bash -n`, checksum checks, and dry-run commands.
- Level 2, lightweight and local: tests that use temporary directories and synthetic small files, after confirming they do not invoke model or scheduler workloads.
- Level 3, requires explicit approval and suitable resources: NetCDF-wide scans, large basin/dam processing, OpenBench evaluation, CoLM executables, MPI, or scheduler submission.

When reporting success, name the highest level actually completed. Passing Level 1 or Level 2 does not establish Level 3 correctness.
