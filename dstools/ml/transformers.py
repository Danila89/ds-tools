from sklearn.base import BaseEstimator, TransformerMixin
import numpy as np
import pandas as pd
import six


def zeroing_candidates(data, threshold, top):
    vc = data.value_counts()
    candidates = set(vc[vc <= threshold].index).union(set(vc[top:].index))
    return candidates


class HighCardinalityZeroing(BaseEstimator, TransformerMixin):
    """
    >>> df = pd.DataFrame({'A': ['a', 'b', 'b', 'a', 'a']})
    >>> HighCardinalityZeroing(2).fit_transform(df).A.tolist()
    ['a', 'zeroed', 'zeroed', 'a', 'a']
    >>> df = pd.DataFrame({'A': ['a', 'b', 'b', 'a', 'a', 'c', 'c']})
    >>> HighCardinalityZeroing(top=2).fit_transform(df).A.tolist()
    ['a', 'b', 'b', 'a', 'a', 'zeroed', 'zeroed']
    """

    def __init__(self, threshold=1, top=10000, placeholder='zeroed', columns=None, n_jobs=1):
        self.zero_categories = dict()
        self.threshold = threshold
        self.top = top
        self.placeholder = placeholder
        self.columns = columns
        self.n_jobs = n_jobs

    def fit(self, df, y=None):
        from sklearn.externals.joblib import Parallel, delayed

        if self.columns is None:
            columns = df.select_dtypes(include=['object'])
        else:
            columns = self.columns

        self.zero_categories = dict(zip(columns, Parallel(n_jobs=self.n_jobs)(
            delayed(zeroing_candidates)(df[col], self.threshold, self.top)
            for col in columns
        )))

        return self

    def transform(self, X):
        res = X.copy()
        for col, candidates in self.zero_categories.items():
            res[col] = res[col].map(lambda x: self.placeholder if x in candidates else x)
        return res


def df2dict():
    from sklearn.preprocessing import FunctionTransformer
    return FunctionTransformer(
        lambda x: x.to_dict(orient='records'), validate=False)


class CountEncoder(BaseEstimator, TransformerMixin):
    """
    >>> df = pd.DataFrame({'A': ['a', 'b', 'b', 'a', 'a', np.nan]})
    >>> CountEncoder().fit_transform(df).A.tolist()
    [0, 1, 1, 0, 0, 2]
    """

    def __init__(self):
        self.vc = dict()

    def fit(self, df, y=None):
        for col in df.select_dtypes(include=['object']):
            # don't use value_counts(dropna=True)!!!
            # in case if joblib n_jobs > 1 the behavior of np.nan key is not stable
            entries = df[col].replace(np.nan, 'nan').value_counts()
            entries = entries.sort_values(ascending=False).index
            self.vc[col] = dict(zip(entries, range(len(entries))))

        return self

    def transform(self, X):
        res = X.copy()
        for col, mapping in self.vc.items():
            res[col] = res[col].map(lambda x: mapping.get(x, mapping.get('nan', 0)))
        return res


def build_categorical_feature_encoder_mean(column, target, reg_threshold):
    global_mean = target.mean()
    col_dna = column.fillna('nan')
    means = target.groupby(col_dna).mean()
    counts = col_dna.groupby(col_dna).count()
    category_shares = counts / counts.sum()
    reg = pd.DataFrame(category_shares / reg_threshold)
    reg[1] = 1.
    reg = reg.min(axis=1)
    means_reg = means * reg + (1-reg) * global_mean
    entries = means_reg.sort_values(ascending=False).index

    encoder = dict(zip(entries, range(len(entries))))
    return encoder


class TargetMeanEncoder(BaseEstimator, TransformerMixin):
    def __init__(self, columns=None, n_jobs=1, reg_threshold=.00001, true_label=None):
        self.vc = dict()
        self.columns = columns
        self.n_jobs = n_jobs
        self.reg_threshold = reg_threshold
        self.true_label = true_label

    def fit(self, df, y):
        from sklearn.externals.joblib import Parallel, delayed

        if self.columns is None:
            columns = df.select_dtypes(include=['object'])
        else:
            columns = self.columns

        if self.true_label is not None:
            target = (y == self.true_label)
        else:
            target = y

        self.vc = dict(zip(columns, Parallel(n_jobs=self.n_jobs)(
            delayed(build_categorical_feature_encoder_mean)(df[col], target, self.reg_threshold)
            for col in columns
        )))

        return self

    def transform(self, df):
        res = df.copy()
        for col, mapping in self.vc.items():
            res[col] = res[col].map(lambda x: mapping.get(x, mapping.get('nan', 0)))
        return res


class MultiClassTargetShareEncoder(BaseEstimator, TransformerMixin):
    def __init__(self, columns=None, n_jobs=1, reg_threshold=.00001):
        self.class_encodings = dict()
        self.columns = columns
        self.n_jobs = n_jobs
        self.reg_threshold = reg_threshold

    def fit(self, df, y):
        from sklearn.externals.joblib import Parallel, delayed

        encoded_classes = pd.Series(y).value_counts().index[1:]

        if self.columns is None:
            self.columns = df.select_dtypes(include=['object'])

        for cl in encoded_classes:
            vc = dict(zip(self.columns, Parallel(n_jobs=self.n_jobs)(
                delayed(build_categorical_feature_encoder_mean)(df[col], pd.Series(y == cl), self.reg_threshold)
                for col in self.columns
            )))
            self.class_encodings[cl] = vc

        return self

    def transform(self, df):
        res = df.copy()
        for cls, cols in self.class_encodings.items():
            for col, mapping in cols.items():
                res['{}_{}'.format(col, cls)] = res[col].map(lambda x: mapping.get(x, mapping.get('nan', 0)))

        res = res.drop(self.columns, axis=1)
        return res


def field_list_func(df, field_names, drop_mode=False, ignore_case=True):
    if ignore_case:
        field_names = map(six.u, field_names)
        field_names = map(lambda e: e.lower(), field_names)

        df_cols = map(six.u, df.columns)
        df_cols = map(lambda e: e.lower(), df_cols)

        col_indexes = [df_cols.index(f) for f in field_names]
        cols = df.columns[col_indexes]
    else:
        cols = field_names

    if drop_mode:
        return df.drop(cols, axis=1)
    else:
        return df[cols]


def field_list(field_names, drop_mode=False, ignore_case=True):
    """
    >>> df = pd.DataFrame(np.arange(9).reshape((3, -1)), columns=['A', 'B', 'C'])
    >>> field_list(['a', 'b']).transform(df).columns.tolist()
    ['A', 'B']
    """
    from sklearn.preprocessing import FunctionTransformer
    from functools import partial
    f = partial(field_list_func, field_names=field_names, drop_mode=drop_mode, ignore_case=ignore_case)
    return FunctionTransformer(func=f, validate=False)


def days_to_delta_func(df, column_names, base_column):
    res = df.copy()
    base_col_date = pd.to_datetime(df[base_column], errors='coerce')
    for col in column_names:
        days_open = (base_col_date - pd.to_datetime(res[col], errors='coerce')).dropna().dt.days
        res[col] = days_open # insert is performed by index hence missing records are not written
    return res


def days_to_delta(column_names, base_column):
    """
    >>> df = pd.DataFrame({'A': ['2015-01-02', '2016-03-20', '42'], 'B': ['2016-02-02', '2016-10-22', '2016-10-22']})
    >>> days_to_delta(['A'], 'B').fit_transform(df).A.fillna(-999).tolist()
    [396.0, 216.0, -999.0]
    """
    from sklearn.preprocessing import FunctionTransformer
    from functools import partial
    f = partial(days_to_delta_func, column_names=column_names, base_column=base_column)
    d2d = FunctionTransformer(func=f, validate=False)
    return d2d
