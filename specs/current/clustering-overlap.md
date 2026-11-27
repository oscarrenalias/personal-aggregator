# clustering-overlap

## Requirement: Normalised entity overlap
<!-- source: spec-499532a1 -->
The clusterer SHALL compare entities after `normalize_entity`, which applies NFKC, casefold, whitespace collapse and surrounding-punctuation removal, and drops a leading "the ". Merge candidate scoring SHALL use the union of entities from each thread's 3 most recent member articles.

### Scenario: Case and spacing differences still overlap
- **WHEN** thread A's latest members have entity `"NVIDIA"` and thread B's have `" Nvidia "`
- **THEN** their entity overlap counts the shared entity

### Scenario: Empty after normalisation is dropped
- **WHEN** an entity is `"  ''  "` or `"—"`
- **THEN** `normalize_entity` yields `""` and it is not added to the entity set

### Scenario: Merge scoring uses recent members
- **WHEN** a thread's latest article has no entities, but one of its previous two members has `["Jensen Huang"]`
- **THEN** that thread's entity set for merge scoring contains `"jensen huang"`
