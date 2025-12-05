"""Data cleaning utilities"""

import pandas as pd


def split_formats(df: pd.DataFrame) -> pd.DataFrame:
    """Create tidy rows of books with multiple formats

    Args:
        df (pd.DataFrame): raw book data

    Returns:
        pd.DataFrame: book data with one format per row
    """
    if "format" not in df.columns:
        raise ValueError("DataFrame must contain a 'format' column")

    # Split the 'format' column by comma and explode into multiple rows
    df["format"] = df["format"].str.split(",")
    df = df.explode("format")

    # Strip whitespace from format entries
    df["format"] = df["format"].str.strip()
    return df


def split_authors(df: pd.DataFrame) -> pd.DataFrame:
    """Create additional columns for books with more than one author

    Args:
        df (pd.DataFrame): raw book data

    Returns:
        pd.DataFrame: book data with 1 author per column
    """
    if "Author" not in df.columns:
        raise ValueError("DataFrame must contain an 'Author' column")

    author_splits = (
        df["Author"]
        .str.split(";", expand=True)
        .applymap(lambda x: x.strip() if isinstance(x, str) else x)
    )

    # Rename the new columns with numbers
    author_splits.columns = [f"Author_{i+1}" for i in range(author_splits.shape[1])]

    # Drop the original 'Author' column and add the new author columns
    df = pd.concat([df.drop(columns=["Author"]), author_splits], axis=1)
    return df


def split_narrators(df: pd.DataFrame) -> pd.DataFrame:
    """Create additional columns for audiobooks with more than 1 narrator

    Args:
        df (pd.DataFrame): enhanced audiobook data

    Returns:
        pd.DataFrame: audiobook data with 1 narrator per column
    """
    if "Narrator" not in df.columns:
        raise ValueError("DataFrame must contain a 'Narrator' column")

    narrator_splits = (
        df["Narrator"]
        .str.split(";", expand=True)
        .applymap(lambda x: x.strip() if isinstance(x, str) else x)
    )

    # Rename the new columns with numbers
    narrator_splits.columns = [
        f"Narrator_{i+1}" for i in range(narrator_splits.shape[1])
    ]

    # Drop the original 'Narrator' column and add the new narrator columns
    df = pd.concat([df.drop(columns=["Narrator"]), narrator_splits], axis=1)
    return df
