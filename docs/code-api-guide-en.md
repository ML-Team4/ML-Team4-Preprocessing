# Preprocessing code and external APIs

This guide explains the APIs used by the preprocessing scripts and what their key arguments control. NumPy and SciPy appear in the lecture PDFs. The notes below cover the specific operations used here.

## Processing architecture

`raw_inputs` binds the raw files with explicit types. `audit_raw_data` defines the fixed normalization macro and bounded audit queries. `preprocess_events` verifies inputs, creates cleaned events and UTC splits, and records quality flags. `build_dataset` creates training-only IDs, counts, popularity, evaluation targets, and the shared CSV package. `validate_dataset` independently recomputes expectations from raw events. `use_dataset` checks the model-facing loader. `export_raw_csv`, `package_dataset`, and `verify_distribution` handle lossless distribution. `analyze_listening` and `verify_listening_analysis` produce and independently check EDA outputs. `review_temporal_split` checks boundaries and exclusions; `check_model_readiness` measures data support rather than model performance.

## DuckDB and SQL

[DuckDB Python API](https://duckdb.org/docs/stable/clients/python/overview)

- `connect(database)` opens the working database. A temporary file database permits disk-backed processing. `execute(sql, parameters)` runs SQL with bound values; parameter binding is for values, not arbitrary SQL identifiers. `close()` releases the connection.
- `fetchone()` retrieves one aggregate row; `fetchall()` is reserved for bounded results. `description` provides result-column names. `fetch_record_batch(rows_per_batch)` streams Arrow batches instead of materializing the entire result as Python records.
- `read_csv(path, header, columns, ...)` fixes raw column types. `read_parquet(path)` reads the legacy input. `create_view(name)` exposes a relation to subsequent SQL without overwriting the source.
- `nfc_normalize(value)` composes canonically equivalent Unicode sequences. `regexp_replace(value, pattern, replacement, 'g')` replaces all matches. The explicit 29-code-point whitespace pattern avoids differences between regular-expression engines. `trim(value)` removes remaining edge spaces. `lower` and `regexp_matches` are used for diagnostics, not identity normalization.
- `GROUP BY` groups complete tuples; `COUNT(*)` counts retained events and `COUNT(DISTINCT user_id)` counts listeners. `SUM` totals counts. `FILTER` limits an aggregate to a condition. `CASE` assigns mutually exclusive reasons in a defined priority order.
- `arg_min(value, ordering_key)` selects one value by an ordering key. Both original names are packed together with `struct_pack`, preventing an invented artist/title combination. `encode(name)` supplies deterministic UTF-8 byte ordering. `row_number() OVER (ORDER BY ...)` assigns IDs; subtracting one creates zero-based indices. `QUALIFY` filters window-function results.
- `JOIN` connects IDs and names; `LEFT JOIN` retains unmatched rows for exclusion classification. `SEMI JOIN` tests membership; `ANTI JOIN` detects missing or extra records. `EXCEPT ALL` preserves multiplicity when comparing raw exports. Ordinary set comparison alone would miss loss of a duplicate row.
- `quantile_cont(value, q)` interpolates a quantile at probability q. These diagnostic quantiles are not deletion thresholds. `md5` checks the documented deterministic cohort ordering, not cryptographic integrity. SHA256 is used for file integrity.
- `COPY ... TO` exports query results with explicit CSV/Parquet options. Timestamp filters use `>= start` and `< end`; UTC intervals are fixed. CTEs and temporary relations separate query stages. Table names passed between helpers come from internal constants, not unchecked user text.

## Arrow storage and CSV handling

[PyArrow CSV](https://arrow.apache.org/docs/python/csv.html)

`pa.schema` and `pa.field(name, type, nullable)` define columns. `int32`, `int64`, `string`, and timestamp types preserve the contract. IDs use int32; original user IDs and counts use int64. `CSVWriter(stream, schema)` writes batches through `write_batch(batch)`. `RecordBatch.from_arrays(arrays, schema)` attaches the expected schema. `Table.from_batches` joins batches, `combine_chunks` consolidates chunks, and `to_batches` returns record batches. `slice(offset, length)` selects a bounded span; `equals(other)` checks exact batch values and types. `num_rows`, `column_names`, `schema.names`, and `null_count` support structural checks.

`open_csv(path, read_options, parse_options, convert_options)` streams records. `read_csv` loads the model-facing tables. `ReadOptions(block_size=...)` bounds input blocks. `ParseOptions(newlines_in_values=True)` permits quoted names with embedded newlines. `ConvertOptions(column_types=..., null_values=[], strings_can_be_null=False)` prevents names such as NA and NULL from becoming missing values. `column.to_numpy()` exposes numerical columns; `to_pylist()` is used for small examples. Parquet readers inspect schema and batches for legacy compatibility, while `write_table(table, path, ...)` creates synthetic or converted files.

## NumPy and SciPy

[NumPy reference](https://numpy.org/doc/stable/reference/) · [SciPy CSR](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.csr_matrix.html)

`asarray` converts an array-like object; `astype(dtype)` explicitly changes its type. `zeros`, `ones`, `empty`, and `arange` allocate or enumerate arrays with specified shapes/types. `iinfo(dtype)` checks representable integer bounds. `unique(values)` obtains distinct values; `array_equal(a, b)` verifies exact equality. `any` and `all` reduce Boolean conditions, while Boolean indexing selects matching observations. `sum`, `min`, `max`, `mean`, `median`, and `quantile(values, q)` summarize numerical arrays. Quantiles use interpolation and may be fractional.

`bincount(indices, minlength=n)` counts each index, retaining empty bins. Once user–track coordinates are unique, counting track indices gives distinct-listener popularity. `add.at(totals, indices, counts)` accumulates repeated indices correctly into int64 totals. `histogram(values, bins)` counts fixed intervals; bin boundaries are part of the published specification. `concatenate` joins batches. `diff` checks successive coordinate/order differences, and `searchsorted(sorted_values, values)` locates insertion positions in sorted data. `isfinite` rejects invalid numerical values. `log1p(x)` computes log(1+x); raw exported counts are not pretransformed.

`csr_matrix((counts, (user_indices, item_indices)), shape=(U, I))` builds the sparse user-by-track matrix. `nnz` counts stored coordinates; `indices`, `indptr`, and `data` represent column positions, row boundaries, and values. Validate these stored arrays before loading: a consumer can discard trailing malformed data. `getrow(index)` obtains one user's history; `load_npz(path)` and `save_npz(path, matrix)` support the legacy sparse package. Dense user-by-track and track-by-track matrices are not needed for the common export.

## Plotting

[Matplotlib API](https://matplotlib.org/stable/api/index.html)

`subplots(nrows, ncols, figsize)` creates a figure and axes. `bar(x, height, ...)` draws counts or percentages; `set_xlabel`, `set_ylabel`, `set_title`, and tick setters name the quantities and categories. `text(x, y, label, ...)` annotates a value; `legend` identifies series and `grid` adds reading guides. `tight_layout` adjusts spacing. `savefig(path, dpi, ...)` selects the output file and resolution; `close(fig)` releases figure resources. Figure metadata records the population and denominator, which must agree with saved tables.

## Python runtime and standard library

[Python library reference](https://docs.python.org/3/library/)

- `argparse.ArgumentParser`, `add_argument(name, type, default, action, ...)`, and `parse_args()` define typed command-line options. Each script's `--help` lists its options. `--config` selects TOML paths; `--output-root` selects a new package parent; `--package` and `--data-dir` select existing inputs.
- `pathlib.Path` joins paths with `/`, resolves them with `resolve`, checks containment with `is_relative_to`, and reads metadata with `stat`. `open`, `read_text`, and `write_text` use explicit encodings where text is involved. `mkdir(parents, exist_ok)` controls directory creation. `glob`, `iterdir`, `exists`, and `is_file` inspect inventories.
- `tomllib.load(file)` reads binary TOML configuration. Relative paths are resolved against the configuration directory, not the caller's working directory.
- `hashlib.file_digest(stream, 'sha256')` and incremental hash updates read file streams; `hexdigest()` produces the hexadecimal identifier. Equal hashes do not establish the semantic correctness of the dataset, so independent record comparisons are also required.
- `csv.reader`, `DictReader`, `writer`, and `DictWriter` handle quoting, headers, and embedded newlines. Files use `newline=''` for CSV processing. `codecs.BOM_UTF8` writes the BOM explicitly. `json.load/loads` parse metadata; `dump/dumps` serialize it with controlled Unicode and indentation.
- `tempfile.TemporaryDirectory(dir=...)` isolates temporary databases and cleans up on context exit. `contextmanager` turns a yielding function into a context manager, ensuring connection cleanup even on failure. `shutil` copies files or removes temporary directory trees.
- `os.link(source, destination)` publishes without replacing an existing file. Rename/error handling protects completed packages against competing writers; `errno` distinguishes filesystem error conditions. `zipfile.ZipFile(path, mode, compression)` reads or writes an archive, with `write(file, arcname)` controlling the member path.
- `subprocess.run(args, cwd, capture_output, text, check)` executes an argument list without shell interpretation. Git inspection reads provenance; consumer examples validate generated outputs. Process-control tests deliberately terminate child processes to exercise partial-write recovery.
- `datetime`, `date`, `timedelta`, and `timezone.utc` express UTC boundaries and elapsed dates. `time` measures runtime; `resource.getrusage` records process resource use. `platform` and `sys` describe the interpreter and execution environment.
- `re.fullmatch(pattern, value)` validates an entire identifier, while substitutions escape or normalize permitted strings. `unicodedata.normalize('NFC', value)` supplies an independent Python normalization reference. `itertools.zip_longest(a, b)` detects unequal batch counts instead of silently truncating comparison.
- `unittest.TestCase` provides assertions and setup/cleanup hooks; `subTest` identifies boundary cases. `unittest.mock.patch` temporarily substitutes test-only inputs. `assertRaises` checks expected rejection; equality assertions compare outputs. Synthetic fixtures test boundary timestamps, duplicate multiplicity, future leakage, unusual names, corrupted metadata, and retries.

## Attribution and verification scope

API links explain library behavior; they do not assert that third-party implementation code was copied. Preserve any specific attribution attached to reused code. The existing model-readiness report separately cites the Item-based CF and implicit-feedback papers for conceptual analysis. Input verification covers the supplied extract and distribution files, not a checksum verification of the complete upstream ListenBrainz TAR.
