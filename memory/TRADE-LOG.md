# Trade log

Append-only, human-readable record of trades. The foundation milestone
does not place trades; this file exists so future milestones have a
stable location.

Format (when trades begin):

```
## YYYY-MM-DD HH:MM:SS UTC <symbol> <side>
- intent: ...
- decision: ...
- result: ...
- notes: ...
```
