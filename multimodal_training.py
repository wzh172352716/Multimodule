import json
import os
import random
from time import perf_counter

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torchvision.transforms as transforms
from PIL import Image
from sklearn.metrics import classification_report
from torch.optim import AdamW
from torch.utils.data import DataLoader, Dataset, RandomSampler, SequentialSampler
from tqdm.auto import tqdm, trange
from transformers import AutoTokenizer, get_scheduler

from vl_model import create_model


class VLDataset(Dataset):
    def __init__(
        self,
        df,
        label_to_id,
        train=False,
        text_field="text",
        label_field="label",
        image_path_field=None,
        image_model_type=None,
    ):
        self.df = df.reset_index(drop=True)
        self.label_to_id = label_to_id
        self.train = train
        self.text_field = text_field
        self.label_field = label_field
        self.image_path_field = image_path_field
        self.image_model_type = image_model_type

        # text only dataset
        if image_model_type is not None:

            # ResNet-50 and ALBEF use different image sizes

            if image_model_type.lower() == "resnet":
                # ResNet-50 settings
                self.img_size = 224

            elif image_model_type.lower() == "albef":
                # ALBEF settings
                self.img_size = 256

            self.mean, self.std = (0.48145466, 0.4578275, 0.40821073), (
                0.26862954,
                0.26130258,
                0.27577711,
            )

            self.train_transform_func = transforms.Compose(
                [
                    transforms.RandomResizedCrop(self.img_size, scale=(0.5, 1.0)),
                    transforms.RandomHorizontalFlip(),
                    transforms.ToTensor(),
                    transforms.Normalize(self.mean, self.std),
                ]
            )

            self.eval_transform_func = transforms.Compose(
                [
                    transforms.Resize(256),
                    transforms.CenterCrop(self.img_size),
                    transforms.ToTensor(),
                    transforms.Normalize(self.mean, self.std),
                ]
            )

    def __getitem__(self, index):
        text = str(self.df.at[index, self.text_field])
        label = self.label_to_id[self.df.at[index, self.label_field]]

        # return images only if image model is specified
        if self.image_model_type is not None:
            img_path = self.df.at[index, self.image_path_field]

            image = Image.open(img_path)
            # print(f"图像路径: {img_path}, 模式: {image.mode}")  # 输出图像路径和类型
            # 如果图像是 RGBA 模式，将其转换为 RGB 模式
            if image.mode == 'RGBA':
                image = image.convert('RGB')
            if self.train:
                img = self.train_transform_func(image)
            else:
                img = self.eval_transform_func(image)

            return text, label, img

        else:
            return text, label

    def __len__(self):
        return self.df.shape[0]


class VLClassifier:
    def __init__(
        self, model=None, tokenizer=None, image_model_type=None, label_map=None
    ):
        self.model = model
        self.tokenizer = (
            AutoTokenizer.from_pretrained("bert-base-uncased")
            if tokenizer is None
            else tokenizer
        )
        self.image_model_type = image_model_type
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.label_to_id = label_map
        self.id_to_label = (
            {v: k for k, v in self.label_to_id.items()}
            if self.label_to_id is not None
            else None
        )
        if self.model is not None:
            self.model.to(self.device)
        print(f"Using device: {self.device}")

    def train(self, df_train, training_args):
        self.training_args = training_args
        batch_size = training_args.get("batch_size")
        num_train_epochs = training_args.get("num_train_epochs")
        learning_rate = training_args.get("learning_rate")
        weight_decay = training_args.get("weight_decay")
        warmup_steps = training_args.get("warmup_steps")
        max_seq_length = training_args.get("max_seq_length")
        text_field = training_args.get("text_field")
        label_field = training_args.get("label_field")
        image_path_field = training_args.get("image_path_field")

        self.label_to_id = {
            lab: i for i, lab in enumerate(df_train[label_field].unique())
        }
        self.id_to_label = {v: k for k, v in self.label_to_id.items()}
        self.num_labels = len(self.label_to_id)

        self.model = create_model(
            self.image_model_type, self.num_labels, text_pretrained="bert-base-uncased"
        )
        self.model.to(self.device)

        train_dataset = VLDataset(
            df=df_train,
            label_to_id=self.label_to_id,
            train=True,
            text_field=text_field,
            label_field=label_field,
            image_path_field=image_path_field,
            image_model_type=self.image_model_type,
        )
        train_sampler = RandomSampler(train_dataset)
        train_dataloader = DataLoader(
            dataset=train_dataset, batch_size=batch_size, sampler=train_sampler
        )

        t_total = len(train_dataloader) * num_train_epochs

        optimizer = AdamW(
            self.model.parameters(), lr=learning_rate, weight_decay=weight_decay
        )
        scheduler = get_scheduler(
            name="cosine",
            optimizer=optimizer,
            num_warmup_steps=warmup_steps,
            num_training_steps=t_total,
        )

        tr_loss = 0.0

        criterion = nn.CrossEntropyLoss()

        self.model.train()
        start = perf_counter()
        for epoch_num in trange(num_train_epochs, desc="Epochs"):
            epoch_total_loss = 0

            for step, batch in tqdm(
                enumerate(train_dataloader), total=len(train_dataloader), desc="Batch"
            ):
                if self.image_model_type is None:
                    b_text, b_labels = batch
                    b_imgs = None
                else:
                    b_text, b_labels, b_imgs = batch

                b_inputs = self.tokenizer(
                    list(b_text),
                    truncation=True,
                    max_length=max_seq_length,
                    return_tensors="pt",
                    padding=True,
                )

                b_labels = b_labels.to(self.device)
                b_inputs = b_inputs.to(self.device)

                if b_imgs is not None:
                    b_imgs = b_imgs.to(self.device)

                self.model.zero_grad()

                if b_imgs is None:
                    b_logits = self.model(text=b_inputs)
                else:
                    b_logits = self.model(text=b_inputs, image=b_imgs)

                loss = criterion(b_logits, b_labels)

                epoch_total_loss += loss.item()

                # Perform a backward pass to calculate the gradients
                loss.backward()

                optimizer.step()
                scheduler.step()

            tr_loss += epoch_total_loss
            avg_loss = epoch_total_loss / len(train_dataloader)

            print("epoch =", epoch_num)
            print("    epoch_loss =", epoch_total_loss)
            print("    avg_epoch_loss =", avg_loss)
            print("    learning rate =", optimizer.param_groups[0]["lr"])

        end = perf_counter()
        training_time = end - start
        print("Training completed in ", training_time, "seconds")

    def predict(self, df_test, eval_args):
        batch_size = eval_args.get("batch_size")
        max_seq_length = eval_args.get("max_seq_length")
        text_field = eval_args.get("text_field")
        image_path_field = eval_args.get("image_path_field")
        label_field = eval_args.get("label_field", None)

        prediction_results = []

        test_dataset = VLDataset(
            df=df_test,
            label_to_id=self.label_to_id,
            train=False,
            text_field=text_field,
            label_field=label_field,
            image_path_field=image_path_field,
            image_model_type=self.image_model_type,
        )
        test_sampler = SequentialSampler(test_dataset)
        test_dataloader = DataLoader(
            dataset=test_dataset, batch_size=batch_size, sampler=test_sampler
        )

        for batch in tqdm(test_dataloader):
            self.model.eval()

            if self.image_model_type is None:
                b_text, b_labels = batch
                b_imgs = None

            else:
                b_text, b_labels, b_imgs = batch

            b_inputs = self.tokenizer(
                list(b_text),
                truncation=True,
                max_length=max_seq_length,
                return_tensors="pt",
                padding=True,
            )

            b_inputs = b_inputs.to(self.device)
            b_labels = b_labels.to(self.device)
            if b_imgs is not None:
                b_imgs = b_imgs.to(self.device)

            with torch.no_grad():
                if b_imgs is None:
                    b_logits = self.model(text=b_inputs)
                else:
                    b_logits = self.model(text=b_inputs, image=b_imgs)

                b_logits = b_logits.detach().cpu()

            prediction_results += torch.argmax(b_logits, dim=-1).tolist()

        prediction_labels = [self.id_to_label[p] for p in prediction_results]

        return prediction_labels

    def save(self, save_directory):
        os.makedirs(save_directory, exist_ok=True)
        model_sd_filepath = os.path.join(save_directory, "state_dict.pt")
        torch.save(self.model.state_dict(), model_sd_filepath)

        label_map_filepath = os.path.join(save_directory, "label_map.json")
        with open(label_map_filepath, "w") as f:
            json.dump(self.label_to_id, f)

        parameters = self.training_args.copy()
        parameters["image_model_type"] = self.image_model_type
        parameters["num_labels"] = len(self.label_to_id)

        parameters_filepath = os.path.join(save_directory, "parameters.json")
        with open(parameters_filepath, "w") as f:
            json.dump(parameters, f)


def from_pretrained(load_directory):
    label_map_filepath = os.path.join(load_directory, "label_map.json")
    with open(label_map_filepath, "r") as f:
        label_map = json.load(f)

    parameters_filepath = os.path.join(load_directory, "parameters.json")
    with open(parameters_filepath, "r") as f:
        parameters = json.load(f)

    image_model_type = parameters["image_model_type"]
    num_labels = parameters["num_labels"]

    tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")

    model_sd_filepath = os.path.join(load_directory, "state_dict.pt")
    model_sd = torch.load(model_sd_filepath, map_location="cpu")

    model = create_model(image_model_type=image_model_type, num_labels=num_labels)
    model.to("cpu")  # load all models in cpu first
    model.load_state_dict(model_sd, strict=True)

    return VLClassifier(
        model=model,
        tokenizer=tokenizer,
        image_model_type=image_model_type,
        label_map=label_map,
    )


def classifier_train_test(df_train, df_test, classifier_type, output_folder, args):
    classifier_to_image_model_map = {
        "bert": None,
        "bert_resnet": "resnet",
        "albef": "albef",
    }

    image_model_type = classifier_to_image_model_map[classifier_type]
    classifier = VLClassifier(image_model_type=image_model_type)
    classifier.train(df_train, args)
    predictions = classifier.predict(df_test, args)
    class_report = classification_report(
        df_test[args.get("label_field")], predictions, output_dict=True
    )

    with open(output_folder + classifier_type + "_class_report.json", "w") as f:
        json.dump(class_report, f)

    df_out = df_test.copy()
    df_out["prediction"] = predictions
    df_out.to_csv(output_folder + classifier_type + "_predictions.csv", index=False)

    model_save_dir = os.path.join(output_folder, classifier_type)
    os.makedirs(model_save_dir, exist_ok=True)
    classifier.save(model_save_dir)


def set_seed(seed_val):
    random.seed(seed_val)
    np.random.seed(seed_val)
    torch.manual_seed(seed_val)
    torch.cuda.manual_seed_all(seed_val)


def main():
    home_folder = "./CAIXI/"
    data_folder = home_folder + "CAIPING_data/"
    image_folder = data_folder + "images/"
    results_folder = home_folder + "results/"
    os.makedirs(results_folder, exist_ok=True)

    df_train = pd.read_csv(data_folder + "train_split.csv")
    df_test = pd.read_csv(data_folder + "test.csv")

    seed_val = 0

    args = {
        "batch_size": 4,
        "num_train_epochs": 5,
        "learning_rate": 1.0e-5,
        "weight_decay": 0.01,
        "warmup_steps": 0,
        "max_seq_length": 64,
        "text_field": "text",
        "label_field": "label",
        "image_path_field": "img_path",
    }

    # df_train[args["image_path_field"]] = df_train[args["image_path_field"]].apply(
    #     lambda x: os.path.join(image_folder, x)
    # )
    # df_test[args["image_path_field"]] = df_test[args["image_path_field"]].apply(
    #     lambda x: os.path.join(image_folder, x)
    # )

    set_seed(seed_val)
    classifier_train_test(
        df_train,
        df_test,
        classifier_type="bert",
        output_folder=results_folder,
        args=args,
    )
    # set_seed(seed_val)
    # classifier_train_test(
    #     df_train,
    #     df_test,
    #     classifier_type="bert_resnet",
    #     output_folder=results_folder,
    #     args=args,
    # )
    # set_seed(seed_val)
    # classifier_train_test(
    #     df_train,
    #     df_test,
    #     classifier_type="albef",
    #     output_folder=results_folder,
    #     args=args,
    # )


if __name__ == "__main__":
    main()
