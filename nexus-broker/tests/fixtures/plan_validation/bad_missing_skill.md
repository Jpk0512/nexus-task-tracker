# Fixture — node declares fewer skills than SKILL_MAP.md requires

### C1

```yaml
node_id: C1
depends_on: []
downstream_consumers: []
agent_persona: pipeline-async
work_type: meta
goal: do a thing without declaring deployable-engineering
context_files: ["docs/agents/CONTRACT.md"]
acceptance_criteria: ["thing done"]
verification_method:
  type: command
  command: "true"
skills_required: [agent-protocol]
risk_tier: T0
budget: S
irreversible: false
do_not_touch: []
notepad_topic: FIX-C1
```
