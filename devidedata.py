import pandas as pd
from sklearn.model_selection import train_test_split

# 读取train.csv
df = pd.read_csv('CAIXI/CAIPING_data/train.csv')

# 将数据集划分为80%训练集和20%测试集
df_train, df_test = train_test_split(df, test_size=0.2, random_state=42, stratify=df['label'])

# 保存训练集和测试集
df_train.to_csv('CAIXI/CAIPING_data/train_split.csv', index=False)
df_test.to_csv('CAIXI/CAIPING_data/test.csv', index=False)

# 打印划分结果
print("训练集样本数量:", df_train.shape[0])
print("测试集样本数量:", df_test.shape[0])

# 检查类别分布
print("训练集类别分布:\n", df_train['label'].value_counts())
print("测试集类别分布:\n", df_test['label'].value_counts())
