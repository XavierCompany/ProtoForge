# Log Format Recognition Guide

## Supported Formats

| Format | Detection Pattern | Example |
|--------|------------------|---------|
| JSON (structured) | Starts with `{` | `{"level":"ERROR","msg":"timeout"}` |
| Syslog (RFC 5424) | `<priority>version timestamp` | `<34>1 2025-01-15T10:30:00Z host app` |
| Apache/Nginx | IP + date in brackets | `192.168.1.1 - - [15/Jan/2025:10:30:00]` |
| Python traceback | `Traceback (most recent call last)` | Multi-line stack trace |
| Java stack trace | `at com.package.Class.method(File.java:42)` | Exception with stack |
| Plaintext | Catch-all | Free-form log lines |

## Timestamp Normalization

Always normalize timestamps to ISO 8601 (UTC) for correlation:
- Unix epoch → `datetime.fromtimestamp(ts, UTC)`
- Relative (`5m ago`) → Calculate from current time
- Timezone-aware → Convert to UTC

## Severity Mapping

Map vendor-specific levels to standard:
- FATAL / CRITICAL / EMERGENCY → **CRITICAL**
- ERROR / SEVERE / ALERT → **HIGH**
- WARN / WARNING → **MEDIUM**
- INFO / NOTICE → **LOW**
- DEBUG / TRACE / VERBOSE → **DEBUG** (usually ignore)
