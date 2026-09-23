This upload demonstrates the use of tabular data. In this example we use an *xlsx* file in combination with a custom schema. The schema describes what columns in the excel file mean and how NOMAD is expected to parse and map the content accordingly in order to produce a **FAIR** dataset.

This schema is meant as a starting point. You can download the schema file and
extend the schema for your own tables.

The upload contains three files:

- `periodic-table.archive.yaml` defines the **_Element_** schema and how its quantities map to
  the columns of the table.
- `data.xlsx` contains the periodic table data.
- `periodic-table-data.archive.yaml` creates an **_Element_** entry that points to `data.xlsx`.

When the upload is processed, the entry in `periodic-table-data.archive.yaml` triggers the parser,
and every row of `data.xlsx` is parsed into its own entry. You should see all elements as
individual entries (`<Element>_<row>.Element.archive.yaml`) next to the files above.

To parse your own table, create another entry by clicking on the **_create from schema_** button,
pick a name for your entry, and select **_Custom schema_** from the options. Then click on the
search icon, from the dialogue, click on the _**Periodic Table**_ and select **_Element_** from the
dropdown menu. Clicking on `Create` creates the entry; set its `data_file` to your own table to
parse it.

Consult our [documentation on the NOMAD Archive and Metainfo](https://nomad-lab.eu/prod/v1/staging/docs/) to learn more about schemas.
