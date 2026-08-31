from enum import StrEnum


class DifficultyClass(StrEnum):
    CLEAN = "clean"
    FEE_GST_DELTA = "fee_gst_delta"
    REFUND_IN_BATCH = "refund_in_batch"
    CHARGEBACK = "chargeback"
    PARTIAL_SPLIT = "partial_split"
    DATE_OUTSIDE_WINDOW = "date_outside_window"
    DUPLICATE_PAYMENT = "duplicate_payment"
    NARRATION_MISSING_UTR = "narration_missing_utr"
    PAYER_NAME_MISMATCH = "payer_name_mismatch"
    UNRELATED_CREDIT = "unrelated_credit"
    UNMATCHABLE = "unmatchable"
