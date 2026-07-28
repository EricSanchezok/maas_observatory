# Security policy

## Reporting a vulnerability

Do not disclose credentials, private endpoints, unpublished trajectories, or a
security vulnerability in a public issue. Contact the repository maintainers
through the private security-reporting channel configured on the GitHub
repository.

Include the affected version, a minimal reproduction, expected impact, and any
known mitigation. Do not test against infrastructure or accounts you do not
own or have permission to use.

## Supported versions

Security fixes are applied to the latest release and the default branch.

## Data handling

Raw runs are private by default. Only bundles produced by `tooluse-bench
release build` and accepted by `tooluse-bench release validate` are eligible
for public upload. Validation is defense in depth, not a substitute for human
review of a release diff.
