# Place trained AeroShield weights here

Combined training (Celik Kubbe v8 + asd1dw v3):

```bash
python scripts/merge_datasets.py
python scripts/train_celikkubbe.py --model yolo11n.pt --device 0
```

This writes `models/aeroshield.pt`. Classes: F16, BalistikFuze, Helikopter, MiniIHA, Dost.
