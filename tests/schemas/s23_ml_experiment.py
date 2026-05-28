from pydantic import BaseModel


class Dataset(BaseModel):
    name: str
    train_size: int
    val_size: int
    test_size: int
    preprocessing: list[str]


class CustomHead(BaseModel):
    type: str
    hidden_dim: int
    dropout: float
    num_classes: int
    activation: str


class ModelConfig(BaseModel):
    architecture: str
    num_parameters: int
    frozen_layers: int
    custom_head: CustomHead


class Hyperparameters(BaseModel):
    learning_rate: float
    batch_size: int
    epochs: int
    warmup_steps: int
    weight_decay: float
    optimizer: str
    scheduler: str
    gradient_clipping: float
    fp16: bool
    seed: int


class Metrics(BaseModel):
    train_loss: float
    val_loss: float
    val_accuracy: float
    val_f1: float
    test_accuracy: float
    test_f1: float


class Results(BaseModel):
    best_epoch: int
    metrics: Metrics
    confusion_matrix: list[list[int]]
    training_time_hours: float


class Experiment(BaseModel):
    id: str
    name: str
    author: str
    status: str
    dataset: Dataset
    model: ModelConfig
    hyperparameters: Hyperparameters
    results: Results


class MLExperiment(BaseModel):
    experiment: Experiment
