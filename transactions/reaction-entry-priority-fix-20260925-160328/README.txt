Reversible multi-file transaction: Facebook target validation optimization (2026-09-25)

Roles:
- modified-target-validation/: verified current-source copies of the four affected runtime files.
- DIFF_FILE.patch: binary-safe Git patch that reconstructs modified-target-validation from baseline-target-validation.
- VERIFICATION.txt: commands, literal outcomes, hashes, runtime acceptance, limitations, and service state.
- ROLLBACK.sh: restores the four baseline files into a supplied target-directory copy.

The formal dirty working tree was not reset, cleaned, committed, or pushed.
