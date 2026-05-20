# model_server/balance_data.py
"""Balance the training set by oversampling minority classes."""
import pandas as pd
from sklearn.utils import resample

def balance_training_data(
    input_path="datasets/train.csv",
    output_path="datasets/balanced_train.csv",
    target_count=None
):
    df = pd.read_csv(input_path)
    
    # Target count = size of the largest class
    if target_count is None:
        target_count = df['label'].value_counts().max()
        print(f"Target count per class: {target_count}")

    balanced_dfs = []
    for label in df['label'].unique():
        subset = df[df['label'] == label]
        if len(subset) < target_count:
            # Oversample with replacement
            subset_oversampled = resample(
                subset, replace=True, n_samples=target_count, random_state=42
            )
            balanced_dfs.append(subset_oversampled)
        else:
            # Undersample if larger (should not happen if target_count is max)
            subset_undersampled = resample(
                subset, replace=False, n_samples=target_count, random_state=42
            )
            balanced_dfs.append(subset_undersampled)
    
    balanced_df = pd.concat(balanced_dfs).sample(frac=1, random_state=42).reset_index(drop=True)
    balanced_df.to_csv(output_path, index=False)
    print(f"Balanced training set saved to {output_path}")
    print("New label distribution:")
    print(balanced_df['label'].value_counts())

if __name__ == "__main__":
    balance_training_data()