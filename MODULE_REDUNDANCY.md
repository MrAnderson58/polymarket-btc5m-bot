# MODULE_REDUNDANCY

Correlation / MI / overlap / redundancy between module action streams.

## Top redundant pairs

| a | b | corr | MI | overlap | redundancy |
|---|---|---:|---:|---:|---:|
| `replay` | `brain` | 0.7344 | 0.01278 | 0.9988 | 0.8666 |
| `alpha` | `optimizer` | 0.0 | 0.0 | 1.0 | 0.5 |
| `alpha` | `evolution` | 0.0 | 0.0 | 1.0 | 0.5 |
| `alpha` | `features` | 0.0 | 0.0 | 1.0 | 0.5 |
| `alpha` | `validation` | 0.0 | 0.0 | 1.0 | 0.5 |
| `optimizer` | `evolution` | 0.0 | 0.0 | 1.0 | 0.5 |
| `optimizer` | `features` | 0.0 | 0.0 | 1.0 | 0.5 |
| `optimizer` | `validation` | 0.0 | 0.0 | 1.0 | 0.5 |
| `evolution` | `features` | 0.0 | 0.0 | 1.0 | 0.5 |
| `evolution` | `validation` | 0.0 | 0.0 | 1.0 | 0.5 |
| `features` | `validation` | 0.0 | 0.0 | 1.0 | 0.5 |
| `alpha` | `brain` | 0.0 | 0.0 | 0.9986 | 0.4993 |
| `optimizer` | `brain` | 0.0 | 0.0 | 0.9986 | 0.4993 |
| `brain` | `evolution` | 0.0 | 0.0 | 0.9986 | 0.4993 |
| `brain` | `features` | 0.0 | 0.0 | 0.9986 | 0.4993 |

## Correlation matrix

```json
{
  "replay": {
    "replay": 1.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.7344,
    "causality": 0.0647,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "edge": {
    "replay": 0.0,
    "edge": 1.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "alpha": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "optimizer": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 1.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "brain": {
    "replay": 0.7344,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 1.0,
    "causality": 0.109,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "causality": {
    "replay": 0.0647,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.109,
    "causality": 1.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "evolution": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 1.0,
    "features": 0.0,
    "validation": 0.0
  },
  "features": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 1.0,
    "validation": 0.0
  },
  "validation": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 1.0
  }
}
```

## Mutual information

```json
{
  "replay": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.01278,
    "causality": 0.001672,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "edge": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "alpha": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "optimizer": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "brain": {
    "replay": 0.01278,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0016,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "causality": {
    "replay": 0.001672,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0016,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "evolution": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "features": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "validation": {
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.0,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  }
}
```

## Overlap

```json
{
  "replay": {
    "replay": 1.0,
    "edge": 0.0,
    "alpha": 0.9974,
    "optimizer": 0.9974,
    "brain": 0.9988,
    "causality": 0.5455,
    "evolution": 0.9974,
    "features": 0.9974,
    "validation": 0.9974
  },
  "edge": {
    "replay": 0.0,
    "edge": 1.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.4543,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "alpha": {
    "replay": 0.9974,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 1.0,
    "brain": 0.9986,
    "causality": 0.5443,
    "evolution": 1.0,
    "features": 1.0,
    "validation": 1.0
  },
  "optimizer": {
    "replay": 0.9974,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 1.0,
    "brain": 0.9986,
    "causality": 0.5443,
    "evolution": 1.0,
    "features": 1.0,
    "validation": 1.0
  },
  "brain": {
    "replay": 0.9988,
    "edge": 0.0,
    "alpha": 0.9986,
    "optimizer": 0.9986,
    "brain": 1.0,
    "causality": 0.5457,
    "evolution": 0.9986,
    "features": 0.9986,
    "validation": 0.9986
  },
  "causality": {
    "replay": 0.5455,
    "edge": 0.4543,
    "alpha": 0.5443,
    "optimizer": 0.5443,
    "brain": 0.5457,
    "causality": 1.0,
    "evolution": 0.5443,
    "features": 0.5443,
    "validation": 0.5443
  },
  "evolution": {
    "replay": 0.9974,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 1.0,
    "brain": 0.9986,
    "causality": 0.5443,
    "evolution": 1.0,
    "features": 1.0,
    "validation": 1.0
  },
  "features": {
    "replay": 0.9974,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 1.0,
    "brain": 0.9986,
    "causality": 0.5443,
    "evolution": 1.0,
    "features": 1.0,
    "validation": 1.0
  },
  "validation": {
    "replay": 0.9974,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 1.0,
    "brain": 0.9986,
    "causality": 0.5443,
    "evolution": 1.0,
    "features": 1.0,
    "validation": 1.0
  }
}
```

## Redundancy

```json
{
  "replay": {
    "replay": 1.0,
    "edge": 0.0,
    "alpha": 0.4987,
    "optimizer": 0.4987,
    "brain": 0.8666,
    "causality": 0.3051,
    "evolution": 0.4987,
    "features": 0.4987,
    "validation": 0.4987
  },
  "edge": {
    "replay": 0.0,
    "edge": 1.0,
    "alpha": 0.0,
    "optimizer": 0.0,
    "brain": 0.0,
    "causality": 0.2271,
    "evolution": 0.0,
    "features": 0.0,
    "validation": 0.0
  },
  "alpha": {
    "replay": 0.4987,
    "edge": 0.0,
    "alpha": 1.0,
    "optimizer": 0.5,
    "brain": 0.4993,
    "causality": 0.2722,
    "evolution": 0.5,
    "features": 0.5,
    "validation": 0.5
  },
  "optimizer": {
    "replay": 0.4987,
    "edge": 0.0,
    "alpha": 0.5,
    "optimizer": 1.0,
    "brain": 0.4993,
    "causality": 0.2722,
    "evolution": 0.5,
    "features": 0.5,
    "validation": 0.5
  },
  "brain": {
    "replay": 0.8666,
    "edge": 0.0,
    "alpha": 0.4993,
    "optimizer": 0.4993,
    "brain": 1.0,
    "causality": 0.3274,
    "evolution": 0.4993,
    "features": 0.4993,
    "validation": 0.4993
  },
  "causality": {
    "replay": 0.3051,
    "edge": 0.2271,
    "alpha": 0.2722,
    "optimizer": 0.2722,
    "brain": 0.3274,
    "causality": 1.0,
    "evolution": 0.2722,
    "features": 0.2722,
    "validation": 0.2722
  },
  "evolution": {
    "replay": 0.4987,
    "edge": 0.0,
    "alpha": 0.5,
    "optimizer": 0.5,
    "brain": 0.4993,
    "causality": 0.2722,
    "evolution": 1.0,
    "features": 0.5,
    "validation": 0.5
  },
  "features": {
    "replay": 0.4987,
    "edge": 0.0,
    "alpha": 0.5,
    "optimizer": 0.5,
    "brain": 0.4993,
    "causality": 0.2722,
    "evolution": 0.5,
    "features": 1.0,
    "validation": 0.5
  },
  "validation": {
    "replay": 0.4987,
    "edge": 0.0,
    "alpha": 0.5,
    "optimizer": 0.5,
    "brain": 0.4993,
    "causality": 0.2722,
    "evolution": 0.5,
    "features": 0.5,
    "validation": 1.0
  }
}
```
