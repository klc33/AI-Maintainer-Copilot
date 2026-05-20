# model_server/splits.py
"""Time‑stratified dataset splits ensuring all classes appear in every split."""
import os
import pandas as pd
from sklearn.model_selection import train_test_split

def create_splits(df: pd.DataFrame):
    """
    Split sorted by time, but within each temporal cut, stratify by label
    so that every split gets a share of each class.
    """
    n = len(df)
    # Temporal cut points (indices)
    train_end = int(n * 0.70)
    val_end = train_end + int(n * 0.10)
    test_end = val_end + int(n * 0.15)

    # Assign time groups: 0=train, 1=val, 2=test, 3=rag_heldout
    time_group = []
    for i in range(n):
        if i < train_end:
            time_group.append(0)
        elif i < val_end:
            time_group.append(1)
        elif i < test_end:
            time_group.append(2)
        else:
            time_group.append(3)
    df["time_group"] = time_group

    # Split train (group 0) + val (group 1) stratified
    mask_train_val = df["time_group"].isin([0, 1])
    train_val = df[mask_train_val]
    # train proportion within this subset = 70/(70+10) = 0.875
    train_sub, val_sub = train_test_split(
        train_val,
        test_size=1 - 0.70/(0.70+0.10),
        stratify=train_val["label"],
        random_state=42,
    )

    # Split test (group 2) + rag (group 3) stratified
    mask_test_rag = df["time_group"].isin([2, 3])
    test_rag = df[mask_test_rag]
    # test proportion within this subset = 15/(15+5) = 0.75
    test_sub, rag_sub = train_test_split(
        test_rag,
        test_size=1 - 0.15/(0.15+0.05),
        stratify=test_rag["label"],
        random_state=42,
    )

    train = train_sub.drop(columns=["time_group"])
    val = val_sub.drop(columns=["time_group"])
    test = test_sub.drop(columns=["time_group"])
    rag = rag_sub.drop(columns=["time_group"])

    print(f"Train: {len(train)}  Val: {len(val)}  Test: {len(test)}  RAG held‑out: {len(rag)}")
    return train, val, test, rag

def main():
    os.makedirs("datasets", exist_ok=True)
    df = pd.read_csv("datasets/terraform_issues.csv", parse_dates=["closed_at"])
    df = df.sort_values("closed_at").reset_index(drop=True)

    train, val, test, rag = create_splits(df)

    train.to_csv("datasets/train.csv", index=False)
    val.to_csv("datasets/val.csv", index=False)
    test.to_csv("datasets/test.csv", index=False)
    rag.to_csv("datasets/rag_heldout.csv", index=False)

    for name, subset in [("train", train), ("val", val), ("test", test)]:
        print(f"\n{name} label distribution:")
        print(subset["label"].value_counts())

if __name__ == "__main__":
    main()