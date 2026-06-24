# agent-skills Spec Delta

## ADDED Requirements

### Requirement: Agent Runtime Shall Use Equipped Hub Skills

When configured to use the Skills Hub, Sub Agents SHALL build their available skill list from the current tenant's equipped skills for that agent role.

#### Scenario: Equipped hub skill appears in prompt

- **GIVEN** `config/settings.json` contains `"skills_source": "hub"`
- **AND** tenant `T` has skill `测试技能导入` equipped to role `content`
- **WHEN** ContentAgent builds its system prompt
- **THEN** the Available Skills block includes `测试技能导入`
- **AND** the block includes the skill id
- **AND** the block instructs the Agent to call `skills.read` before using a named skill

#### Scenario: Legacy file mode remains available

- **GIVEN** `config/settings.json` does not contain `"skills_source": "hub"`
- **WHEN** an Agent builds its system prompt
- **THEN** it continues to use the legacy file-backed skill list

### Requirement: skills.read Shall Read Equipped Hub Skills

In hub mode, `skills.read` SHALL resolve only skills equipped for the current tenant and current agent role.

#### Scenario: Read by skill id

- **GIVEN** `config/settings.json` contains `"skills_source": "hub"`
- **AND** tenant `T` has skill id `S1` equipped to role `content`
- **WHEN** ContentAgent calls `skills.read` with `scope="content"` and `skill_id="S1"`
- **THEN** the tool returns the full skill body

#### Scenario: Read by exact name

- **GIVEN** `config/settings.json` contains `"skills_source": "hub"`
- **AND** tenant `T` has skill `测试技能导入` equipped to role `content`
- **WHEN** ContentAgent calls `skills.read` with `scope="content"` and `name="测试技能导入"`
- **THEN** the tool returns the full skill body

#### Scenario: Unequipped skill is rejected

- **GIVEN** `config/settings.json` contains `"skills_source": "hub"`
- **AND** tenant `T` owns skill `S2`
- **AND** `S2` is not equipped to role `content`
- **WHEN** ContentAgent calls `skills.read` with `scope="content"` and `skill_id="S2"`
- **THEN** the tool returns `ok=false`
- **AND** the error states that the skill is not equipped for this role

#### Scenario: Scope boundary remains enforced

- **WHEN** ContentAgent calls `skills.read` with `scope="intel"`
- **THEN** the tool returns `ok=false`
- **AND** no skill body is returned

