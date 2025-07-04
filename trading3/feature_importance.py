__all__ = ['feat_importance', 'feat_imp_MDI', 'feat_imp_MDA']

# Cell

import pandas as pd
import numpy as np
import logging

from .utils import PurgedKFold
from sklearn.metrics import log_loss, accuracy_score
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import BaggingClassifier


def feat_importance(
    events,
    X,
    y,
    n_estimators=1000,
    cv=10,
    max_samples=1.0,
    pct_embargo=0,
    scoring="accuracy",
    method="MDI",
    min_w_leaf=0.0,
    **kwargs
):
    logging.info(f"feat_importance for {len(X.columns)} features")
    # 1) prepare classifier, cv. max_features=1 to prevent masking
    # feature importance from a random forest
    clf = DecisionTreeClassifier(
        criterion="entropy",
        max_features=1,
        class_weight="balanced",
        min_weight_fraction_leaf=min_w_leaf,
    )

    clf = BaggingClassifier(
        base_estimator=clf,
        n_estimators=n_estimators,
        max_features=1.0,
        max_samples=max_samples,
        oob_score=True,
    )

    if method == "MDI":
        fit = clf.fit(X=X, y=y)
        imp = feat_imp_MDI(fit, feat_names=X.columns)
    elif method == "MDA":
        sample_weight = pd.Series(1, index=events.index)
        imp = feat_imp_MDA(
            clf=clf,
            X=X,
            y=y,
            cv=cv,
            sample_weight=sample_weight,
            t1=events["t1"],
            pct_embargo=pct_embargo,
            scoring=scoring,
        )

    imp = imp.sort_values("mean", ascending=True)

    return imp


def feat_imp_MDI(fit, feat_names):
    # feat importance based on IS mean impurity reduction
    df0 = {i: tree.feature_importances_ for i, tree in enumerate(fit.estimators_)}
    df0 = pd.DataFrame.from_dict(df0, orient="index")
    df0.columns = feat_names
    df0 = df0.replace(0, np.nan)  # because max_features = 1
    imp = pd.concat(
        {"mean": df0.mean(), "std": df0.std() * df0.shape[0] ** -0.5}, axis=1
    )
    imp /= imp["mean"].sum()
    return imp


def feat_imp_MDA(clf, X, y, cv, sample_weight, t1, pct_embargo, scoring="neg_log_loss"):
    # feat importance based on OOS score reduction
    if scoring not in ["neg_log_loss", "accuracy"]:
        raise ValueError("wrong scoring method")
    from sklearn.metrics import log_loss, accuracy_score
    logging.debug(f"MDA with {cv}-fold CV")

    cv_gen = PurgedKFold(n_splits=cv, t1=t1, pct_embargo=pct_embargo)
    scr0, scr1 = pd.Series(), pd.DataFrame(columns=X.columns)
    for i, (train, test) in enumerate(cv_gen.split(X=X)):
        X0, y0, w0 = X.iloc[train, :], y.iloc[train], sample_weight.iloc[train]
        X1, y1, w1 = X.iloc[test, :], y.iloc[test], sample_weight.iloc[test]
        fit = clf.fit(X=X0, y=y0, sample_weight=w0.values)
        if scoring == "neg_log_loss":
            prob = fit.predict_proba(X1)
            scr0.loc[i] = -log_loss(
                y1, prob, sample_weight=w1.values, labels=clf.classes_
            )
        else:
            pred = fit.predict(X1)
            scr0.loc[i] = accuracy_score(y1, pred, sample_weight=w1.values)

        for j in X.columns:
            X1_ = X1.copy(deep=True)
            np.random.shuffle(X1_[j].values)  # permutation of a single column
            if scoring == "neg_log_loss":
                prob = fit.predict_proba(X1_)
                scr1.loc[i, j] = -log_loss(
                    y1, prob, sample_weight=w1.values, labels=clf.classes_
                )
            else:
                pred = fit.predict(X1_)
                scr1.loc[i, j] = accuracy_score(y1, pred, sample_weight=w1.values)

    imp = (-scr1).add(scr0, axis=0)
    if scoring == "neg_log_loss":
        imp = imp / -scr1
    else:
        imp = imp / (1.0 - scr1)

    imp = pd.concat(
        {"mean": imp.mean(), "std": imp.std() * imp.shape[0] ** -0.5}, axis=1
    )
    return imp