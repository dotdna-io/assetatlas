## Summary

<!-- What does this PR change, and why? Keep it focused on one logical change. -->

## Related issue

<!-- Link the issue this PR resolves, e.g. "Closes #123". -->
Closes #

## Checklist

- [ ] Branched off `main` (not committing directly to `main`).
- [ ] Commits are atomic, with clear, imperative messages.
- [ ] Commits are signed off for the [DCO](https://developercertificate.org/) (`git commit -s`).
- [ ] No `Generated with …` or `Co-Authored-By:` trailer lines.
- [ ] Backend: the CI-gated `pytest` suites pass, `flake8 --select=F811,F821,F601,W605,E9 src/` is clean, and `black --check` passes on the files you changed.
- [ ] Frontend (only if `ui/prod/` changed): `npm run typecheck` and `npm test` pass.
- [ ] Documentation updated if behavior, configuration, or interfaces changed.

<!--
See CONTRIBUTING.md for the full contribution guide.
Report security vulnerabilities privately via SECURITY.md — never in a public PR or issue.
-->
