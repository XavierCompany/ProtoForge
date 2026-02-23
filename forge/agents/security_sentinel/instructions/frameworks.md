# Security Frameworks Reference

## OWASP Top 10 (2021)
| # | Category | Detection Approach |
|---|---------|-------------------|
| A01 | Broken Access Control | Check auth/authz logic, RBAC, IDOR |
| A02 | Cryptographic Failures | Weak ciphers, plaintext secrets, missing TLS |
| A03 | Injection | SQL, NoSQL, OS, LDAP injection patterns |
| A04 | Insecure Design | Missing threat models, unsafe patterns |
| A05 | Security Misconfiguration | Default creds, open ports, verbose errors |
| A06 | Vulnerable Components | CVE database lookup for dependencies |
| A07 | Auth Failures | Weak passwords, missing MFA, session issues |
| A08 | Data Integrity Failures | Unsigned updates, deserialization, CI/CD |
| A09 | Logging Failures | Missing audit logs, log injection |
| A10 | SSRF | URL parsing, internal network access |

## CVSS v3.1 Scoring
| Range | Severity | Action |
|-------|---------|--------|
| 9.0-10.0 | Critical | Immediate remediation required |
| 7.0-8.9 | High | Fix within 24 hours |
| 4.0-6.9 | Medium | Fix within 1 week |
| 0.1-3.9 | Low | Fix in next release |
| 0.0 | Info | Informational only |

## Compliance Frameworks
### SOC 2
- Access control, encryption at rest/transit, audit logging, incident response

### PCI DSS
- Cardholder data protection, network segmentation, key management

### HIPAA
- PHI encryption, access logs, minimum necessary, BAA requirements

## Secret Detection Patterns
```
# High-confidence patterns
API[_-]?KEY\s*[:=]\s*['"][A-Za-z0-9]{20,}
(password|secret|token)\s*[:=]\s*['"][^'"]+
-----BEGIN (RSA |EC )?PRIVATE KEY-----
AKIA[0-9A-Z]{16}          # AWS Access Key
ghp_[a-zA-Z0-9]{36}       # GitHub PAT
```
