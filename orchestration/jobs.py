from dagster import define_asset_job
from etl.enhance import enhanced_book_features


enhance_job = define_asset_job(name="enhance_job", selection=[enhanced_book_features])
