# Design: Optional Person Companion for Daily Scene

Date: 2026-02-19  
Status: Draft for validation before implementation  
Branch: `feat-person-companion-mix`

## 1. Context

`foredogs` generates a daily image from:
- weather forecast
- one or more dogs (names + descriptions)
- dog reference images
- random art style

Current flow:
1. Home Assistant service validates request (`__init__.py`)
2. Request model is parsed (`models.py`)
3. Activity text is generated (`generate_activity` in `foredogs.py`)
4. Image is generated (`generate_image` in `foredogs.py`) using all reference images

## 2. Goal

Add an optional "person companion" feature:
- Base scene remains dog-centric (e.g. the dogs from `dogs.json`)
- With probability `p` (default `0.4`), include exactly one additional person
- Person is selected randomly from a pool of 6-7 candidates
- Matching person reference image is added to image generation context
- Result: ~40% of days become `Dog + Person X`, otherwise `Dog only`

## 3. Scope

In scope:
- Data model extension for people pool + inclusion probability
- Random selection logic
- Prompt updates for activity and image generation
- HA service schema + docs/example automation updates
- Validation and tests for mapping and probability behavior

Out of scope (v1):
- Multiple people in one image
- Weighted person selection (different probabilities per person)
- Separate person art-style constraints
- Persistent "last used person" anti-repeat logic

## 4. Candidate Approaches

### Approach A (Recommended): Parallel Lists + Simple Selector

Add optional request fields:
- `person_names: list[str] = []`
- `person_descriptions: list[str] = []`
- `person_image_paths: list[str] = []` (1:1 mapping by index)
- `person_inclusion_probability: float = 0.4`

Runtime:
- If pool is valid and `random.random() < person_inclusion_probability`, choose one index
- Build `selected_person_context` with name, description, image path
- Inject selected person into activity + image prompts
- Add selected person image to the reference-image payload

Pros:
- Fastest implementation
- Minimal schema and docs change
- Easy to debug in logs

Cons:
- Index-based list mapping is fragile if user misorders lists

### Approach B: Structured People Objects

Add one field:
- `people_pool: list[{"name": str, "description": str, "image_path": str}]`

Runtime identical to A.

Pros:
- Stronger data integrity (no index mismatch)
- Easier future extension (weights, tags, roles)

Cons:
- Requires nested object schema handling in HA service config/docs
- Slightly larger migration effort for existing users

### Approach C: Scenario Templates

Create scenario-level templates:
- `dog_only` and `dog_plus_person`
- First select scenario by probability, then select person for second scenario

Pros:
- Clean separation for future expansion (e.g. dog+person+location modes)

Cons:
- Added indirection now without immediate practical gain
- More complexity for first iteration

## 5. Recommendation

Use **Approach A** for v1.

Reason:
- The operator wants to test quickly "today" with a small pool of people.
- A keeps change size small and preserves current service contract style.
- We can migrate to structured objects later if list-mismatch issues appear.

## 6. Detailed Design (v1)

## 6.1 Data Model

Update `models.py` (`GenerateRequest`):
- Add optional people pool fields:
  - `person_names: list[str] = []`
  - `person_descriptions: list[str] = []`
  - `person_image_paths: list[str] = []`
  - `person_inclusion_probability: float = 0.4`

Validation rules (Pydantic):
- Probability in range `[0.0, 1.0]`
- If any person list is provided, all three lists must be same length
- Empty pool is valid and means "dog-only always"

## 6.2 Service Schema

Update `__init__.py` / `SERVICE_SCHEMA`:
- Add optional fields mirroring model
- Keep default probability at `0.4`
- Keep backward compatibility (existing users without these fields continue working)

Update `services.yaml` and `examples/homeassistant/automation_fragment.yaml`:
- Document new fields and examples for 3+ people
- Explicitly state 1:1 mapping requirement between name/description/image path

## 6.3 Selection Logic

Add helper in `foredogs.py`:
- `select_optional_person(data) -> SelectedPerson | None`

Pseudo-flow:
1. Validate pool completeness and available image paths
2. Roll random number
3. If roll fails threshold or pool empty -> `None`
4. Pick random index from pool
5. Return selected person context

Logging:
- Include roll value, threshold, selected person name (if any)
- Warn on invalid pool entries and fall back to dog-only mode

## 6.4 Prompt Changes

### Activity prompt (`generate_activity`)

Current prompt is dog-only.  
New behavior:
- If person selected: require activity involving dogs + selected person
- If no person selected: existing behavior unchanged

Prompt addition (concept):
- "Today include person `<name>` in the activity and keep dogs central."
- "Do not alter core dog identity from reference images."

### Image prompt (`generate_image`)

If person selected:
- Add section with person name + description
- Include instruction to use selected person reference image for likeness
- Keep existing dog reference constraints

## 6.5 Reference Image Payload

Current payload sends all dog reference images.  
v1 behavior:
- Keep existing dog images as-is
- Append selected person image only when person is included

Important:
- Keep image names explicit in prompt so model can map references correctly

## 6.6 Error Handling

Fallback-to-safe behavior:
- Invalid probability -> validation error at request parse stage
- Mismatched person list lengths -> validation error
- Missing selected person image file -> log warning, continue dog-only
- Empty valid dog references -> existing behavior unchanged

## 6.7 Testing Strategy

Unit tests (new `tests/` module or local test script):
- Probability boundaries (`0.0`, `0.4`, `1.0`)
- Pool mismatch validation
- Deterministic selection with seeded random
- Missing person image fallback

Prompt construction tests:
- Person-selected prompt contains person fields
- Dog-only prompt does not contain person fields

Manual HA test cases:
1. No person fields supplied -> old behavior unchanged
2. Person fields + probability `0.0` -> never include person
3. Person fields + probability `1.0` -> always include one person
4. Person fields + probability `0.4` -> person appears intermittently

## 7. Rollout Plan

1. Add model + schema fields with backward compatibility.
2. Add person selection helper and prompt branching.
3. Update docs + automation example with clear mapping format.
4. Run local validation tests.
5. Perform a Home Assistant manual test with a small person pool.

## 8. Risks and Mitigations

Risk: person/dog likeness degrades when too many references are passed.  
Mitigation: pass only selected person image (not all person images) and keep dog references concise.

Risk: user config mismatch across list fields.  
Mitigation: strict validation and explicit error messages.

Risk: activity text becomes too long with person context.  
Mitigation: preserve single-line and word-limit constraints in prompt.

## 9. Acceptance Criteria

- Existing dog-only users do not need to change config.
- With valid person pool and `0.4` probability, generation includes exactly one person in about 40% of runs (statistical expectation).
- Chosen person matches reference image and appears in both activity and image prompt.
- Invalid person config fails clearly or safely falls back as defined.

## 10. Confirmed Decision

Confirmed on 2026-02-19: **maximum one person per image plus the dog set**.  
No multi-person scenes in v1. Extend only in a future v2 if needed.
