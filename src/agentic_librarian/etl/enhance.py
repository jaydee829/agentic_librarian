import etl.cleaning as cleaning
import mlflow
import pandas as pd

# import os
from dagster import AssetExecutionContext, MetadataValue, asset
from scouts.metadata_scout import AudiobookScout, fetch_google_books_metadata

# from dvc.repo import Repo


@asset
def enhanced_book_features(context: AssetExecutionContext):
    try:
        # Load the raw data
        df = pd.read_csv(f"data/raw/{context.partition_key}.csv")
    except FileNotFoundError:
        context.log.error(f"File data/raw/{context.partition_key}.csv not found.")
        raise Exception(
            f"Sensor triggered but file data/raw/{context.partition_key}.csv is missing."
        ) from None

    df = cleaning.split_formats(df)
    df = cleaning.split_authors(df)

    mlflow.set_experiment("agentic_audiobook_metadata")

    with mlflow.start_run(run_name="gemini_enhancement"):
        audiobook_count = len(df[df["format"] == "audiobook"])
        mlflow.log_param("audiobook_count", audiobook_count)

        scout = AudiobookScout()

        audio_df = pd.DataFrame()
        for row in df.iterrows():
            if row[1]["format"] == "audiobook":  # TODO: check case sensitivity
                # TODO: what about audio drama? Any other audio formats?
                metadata = scout.extract_metadata_with_gemini(row[1]["Title"])
                audio_df = pd.concat(
                    [audio_df, pd.DataFrame([metadata])], ignore_index=True
                )  # TODO: verify schema matches, add to successes if so
            else:
                metadata = fetch_google_books_metadata(row[1]["ISBN_13"])
                for key, value in metadata.items():
                    df.at[row[0], key] = value
        audio_df = cleaning.split_narrators(audio_df)

    # --- THE MAGIC BIT ---
    # Log the "Vital Signs" of your data to the UI
    context.add_output_metadata(
        {
            "row_count": len(df),
            "preview": MetadataValue.md(
                df.head(5).to_markdown()
            ),  # Renders a table in UI
            "missing_values": int(df.isnull().sum().sum()),
            "columns": str(list(df.columns)),
        }
    )

    return df
