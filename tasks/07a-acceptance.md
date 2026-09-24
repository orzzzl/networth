# 07a acceptance — 2026-09-24

The automatic Link lifecycle is implemented and reviewed. PR #109 was approved
at `bf760fb0a1d63e7108090a5cca498b83ea3df311` and merged as
`f524de69b41a2b03e38e2d343c1e5f132fd848ba`. The earlier worker is PR #104;
contracts and component history remain in the linked 07a implementation notes.
Those notes describe the work remaining at their respective merge times; this
record is the current acceptance index.

## Criterion evidence

All test paths below are under `tests/`. Fixtures are synthetic, using real
SQLite, TokenStore and recovery files. This is code acceptance, not a live
installation or a new Sandbox/Production measurement.

| Acceptance | Discriminating evidence |
|---|---|
| Observed session clocks, all not-ready shapes, per-result deadlines | `test_link_worker.py::test_not_ready_shapes_never_invent_result_or_item`, `test_only_observed_finish_controls_exact_exchange_deadline`, `test_each_result_uses_its_own_deadline`; `test_link_observations.py::test_finish_arriving_without_token_fills_its_results_deadlines_only` |
| State projection and Item budget | `test_link_result_storage.py::test_entity_state_classification_is_total`, `test_item_identity_counts_equal_once_distinct_twice`, `test_nameless_result_is_one_slot_except_ambiguous_exchanged`; child success survives parent exit in `test_link_observations.py::test_exit_cannot_erase_other_success_or_its_own_later_success` |
| First observation writer ships with authorized adjudication | PR #94 includes `adjudicate-link-observation`; `test_link_observations.py::test_adjudication_confirmation_and_cli_environment_are_required`, `test_adjudication_ledger_failure_rolls_back_current_resolution` |
| Reachable ABANDONED and URL_EXPIRED without invented zero cost | `test_link_lifecycle.py::test_never_released_request_has_local_no_success_proof`, `test_abandon_and_child_exit_do_not_close_reopenable_url`; native provenance is required |
| Conditional local claim, zero-row refusal, terminal uncertainty | `test_link_worker.py::test_zero_row_claim_never_calls_provider`, `test_stale_sent_claim_without_material_is_uncertain_and_never_retried`, `test_request_lock_is_cross_process_and_held_through_send_and_storage` |
| Credential durability before Item commit; metadata restart/redaction | `test_link_worker.py::test_metadata_outage_restart_finishes_from_material_without_poll_or_exchange`; `test_link_finalization.py::test_restart_after_metadata_outage_reconciles_and_finalizes`, `test_commit_failure_rolls_back_institution_item_and_result`; metadata guards in `test_link_finalization_guards.py` |
| Issue #42: material cannot become an Item identity | `test_link_worker.py::test_credential_shaped_item_id_is_never_persisted_or_passed_to_put`; repaired `test_tokenstore.py::test_no_token_material_reaches_any_database_table` uses the real finalizer and reconciled identity, includes an adversarial material-valued identity, and scans all tables plus open database/WAL bytes |
| Unverified material stays a hold; stale claims reconcile all candidates first | `test_link_reconciliation.py::test_real_pending_durability_failure_persists_a_hold`, `test_unverified_hold_survives_restart_and_cannot_be_reinterpreted`, `test_restart_recovers_final_and_pending_under_both_names`; `test_link_worker.py::test_restart_at_exchange_crash_boundaries_never_sends_twice` |
| Identifiers captured before later failures; no fabricated Item identity | `test_link_worker.py::test_identifiers_precede_put_and_storage_fault_is_persistent_hold`, `test_exchange_exception_is_terminal_with_available_support_ids`; polling ingestion excludes unallowlisted fields |
| Request-scoped cleanup, diagnostics retention, all partial states and concurrency | `test_link_lifecycle.py::test_reaper_repairs_three_partial_states_and_delete_crash`, `test_stranded_children_keep_material_to_latest_diagnostics_deadline`, `test_unknown_diagnostics_deadline_prevents_deletion_after_closure`, `test_reaper_never_deletes_arbitrary_access_reference`, `test_reaper_holds_worker_file_lock_and_sql_write_lock` |
| Retention/outage gaps never manufacture headroom | `test_link_lifecycle.py::test_retention_gap_creates_durable_hold_not_zero_cost_closure`, `test_frequent_empty_polls_do_not_manufacture_completeness`, `test_coverage_slots_survive_reopen_and_nonzero_closure_keeps_success_parent` |
| Duplicate exchange evidence respected | `test_link_worker.py::test_multiple_results_keep_all_credentials_and_count_returned_identity`; finalization tests preserve same-Item credentials and distinct Items; missing attribution remains unresolved |
| Mint, immediate poll, verified second copy, authorized URL release, resume | `test_link_lifecycle.py::test_mint_durable_request_and_immediate_poll_before_release`; `test_automatic_link_driver.py::test_restart_after_committed_release_lost_ack_mints_once`, `test_crash_before_return_leaves_verified_resume_record`, `test_foreign_holder_resume_refused_before_remote_invocation`, `test_literal_v2_record_remains_readable` |

## Validation and limits

The integrated implementation's [exact-head CI](https://github.com/orzzzl/networth/actions/runs/36038779256)
completed successfully at `bf760fb0a1d63e7108090a5cca498b83ea3df311`, with both
`check` and `app` successful. Claude's [approval](https://github.com/orzzzl/networth/pull/109#issuecomment-5823265441)
records a full local run of 1400 passed / 4 skipped and discriminating schema,
protocol and foreign-holder guard mutations. The closure PR also repairs the
remaining table-test obligation from issue #42; its validation is recorded in
that PR rather than attributed to the earlier CI run.

The scope remains Sandbox-only entry points and synthetic integration tests.
No live Plaid call, credential inspection, owner Link rerun, or deployment was
performed for this closure. The completed 06a evidence is unchanged and its
heartbeat remains PAUSED. The accepted duplicate exchange is not a cross-host
fence, and its extra Item cost remains unmeasured.

## Handoff

- 07b becomes READY: 05a, 07a, 03a and 00b-escrow are complete. Claude owns the
  recovery script, durable sink/restore pairing, real power-off fence and its
  required Sandbox rehearsal. Same returned Item means count once; distinct
  Items retain both credentials; unknown identity stays unresolved.
- 16 becomes READY: 10, 12, 14, 15, 07a, 20 and 28 are complete. Codex owns the
  scheduling/live installation, including this request reaper. Completing 07a
  does not claim that any daemon or timer has been installed.
- 08 remains BLOCKED by 07b, 03a-live and 16. Production enablement and the
  owner-only banking steps stay there.
