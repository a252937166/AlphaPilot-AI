# Security Policy

## Reporting

Do not open public issues for vulnerabilities, leaked credentials, brokerage account identifiers or proprietary datasets. Report them privately to the repository owner.

## Mandatory controls

- Secrets are loaded only from environment variables or an approved secret manager.
- Cloud services must never receive brokerage passwords.
- Futu OpenD should run locally or in an isolated trading gateway.
- Live order execution is disabled unless two independent configuration flags are enabled and risk checks pass.
- Every model forecast, alert, trade proposal and order must retain an immutable audit record.
- Market data freshness and source provenance are mandatory inputs to trading decisions.
