# Data

This directory contains all input, output, and log data used by the adapter.
None of the files in this directory are tracked by version control — only the
folder structure itself is committed.

---

## Structure

```
data/
├── input/
│   ├── hpo/        Raw HPO ontology file (hp.owl)
│   ├── sct/        Raw SNOMED CT RF2 release files
│   └── umls/       Raw UMLS RRF release files
├── output/
│   └── transformed/
│       ├── hpo/    EAV CSV output for HPO
│       ├── sct/    EAV CSV output for SNOMED CT
│       └── umls/   EAV CSV output for UMLS
└── logs/           Adapter run logs
```

---

## `input/`

Contains the raw ontology source files for each adapter. Files must be placed
in the correct subdirectory before running an adapter, and their names must
match the `input_files` list in `config/config.yaml`.

### `input/hpo/`

Place the HPO OWL file here:

```
hp.owl
```

The HPO ontology can be downloaded from the
[HPO website](https://hpo.jax.org/data/ontology) or directly from the
[HPO GitHub releases](https://github.com/obophenotype/human-phenotype-ontology/releases).

### `input/sct/`

Place the SNOMED CT RF2 release files here. The adapter expects standard
RF2 filenames; the following file types are processed:

| File prefix | Description |
|---|---|
| `sct2_Concept_` | Concept file (required — used as the active-concept filter) |
| `sct2_Description_` | Fully Specified Names and Synonyms |
| `sct2_TextDefinition_` | Free-text definitions |
| `sct2_Relationship_` | IS-A relationships |
| `der2_sRefset_SimpleMapFull` | Cross-map reference sets (e.g. ICD-10) |

SNOMED CT requires a license from [SNOMED International](https://www.snomed.org).

### `input/umls/`

Place the UMLS Metathesaurus RRF files here. The following files are
processed:

| File | Description |
|---|---|
| `MRCONSO.RRF` | Concept strings (labels, synonyms, source codes) |
| `MRDEF.RRF` | Concept definitions |

UMLS requires a license from the
[U.S. National Library of Medicine (NLM)](https://www.nlm.nih.gov/research/umls/).

---

## `output/transformed/`

Contains the EAV CSV files produced by each adapter after a successful
`load()` and `to_csv()` call. Each subdirectory corresponds to one adapter
and holds a single CSV file whose name is configured in `config/config.yaml`.

The CSV uses the following columns by default (configurable):

| Column | Description |
|---|---|
| `id` | Concept identifier (e.g. `HP:0000118`, `123456789`, `C0001`) |
| `attribute` | EAV attribute name (e.g. `label`, `synonym`, `definition`, `child`, `reference`) |
| `value` | Attribute value (e.g. the label string or the child concept ID) |
| `additional` | JSON object with supplementary metadata (e.g. source abbreviation, synonym type) |

---

## `logs/`

Contains log files written during adapter runs. The log file name and
location are configured under the `logger` section of `config/config.yaml`.