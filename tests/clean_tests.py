import pytest
import pandas as pd

import data.cleaning as cleaning


def test_split_formats_single_format():
    df = pd.DataFrame(
        {"title": ["Book A", "Book B"], "format": ["Hardcover", "Paperback"]}
    )
    result = cleaning.split_formats(df)
    assert len(result) == 2
    assert result.iloc[0]["format"] == "Hardcover"
    assert result.iloc[1]["format"] == "Paperback"


def test_split_formats_multiple_formats():
    df = pd.DataFrame(
        {
            "title": ["Book A", "Book B"],
            "format": ["Hardcover, eBook", "Paperback, Audiobook"],
        }
    )
    result = cleaning.split_formats(df)
    assert len(result) == 4
    assert set(result["format"]) == {"Hardcover", "eBook", "Paperback", "Audiobook"}


def test_split_formats_no_format_column():
    df = pd.DataFrame({"title": ["Book A", "Book B"]})
    with pytest.raises(ValueError, match="DataFrame must contain a 'format' column"):
        cleaning.split_formats(df)


def test_split_authors_single_author():
    df = pd.DataFrame(
        {"title": ["Book A", "Book B"], "Author": ["Author One", "Author Two"]}
    )
    result = cleaning.split_authors(df)
    assert "Author_1" in result.columns
    assert result.iloc[0]["Author_1"] == "Author One"
    assert result.iloc[1]["Author_1"] == "Author Two"
    assert "Author_2" not in result.columns


def test_split_authors_multiple_authors():
    df = pd.DataFrame(
        {
            "title": ["Book A", "Book B"],
            "Author": [
                "Author One; Author Two",
                "Author Three; Author Four; Author Five",
            ],
        }
    )
    result = cleaning.split_authors(df)
    assert "Author_1" in result.columns
    assert "Author_2" in result.columns
    assert "Author_3" in result.columns
    assert result.iloc[0]["Author_1"] == "Author One"
    assert result.iloc[0]["Author_2"] == "Author Two"
    assert pd.isna(result.iloc[0].get("Author_3"))
    assert result.iloc[1]["Author_1"] == "Author Three"
    assert result.iloc[1]["Author_2"] == "Author Four"
    assert result.iloc[1]["Author_3"] == "Author Five"
