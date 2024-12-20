import torch
torch.cuda.empty_cache()
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, AutoModel
from datasets import load_dataset
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

### Temporary changes

# Load chatbot tokenizer and model
# chatbot_tokenizer_path = "facebook/opt-350m"
# chatbot_model_path = "/home/adrian/Documents/Perceptron/model_weights/transfer_learning_chatbot.pth"
# chatbot_tokenizer = AutoTokenizer.from_pretrained(chatbot_tokenizer_path)
model = AutoModelForCausalLM.from_pretrained("facebook/opt-350m")
# chatbot_model.load_state_dict(torch.load(chatbot_model_path)
chatbot_tokenizer = AutoTokenizer.from_pretrained("facebook/opt-350m")


# Prepare model for training
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# chatbot_model.to(device)
# chatbot_model.train() # Set to training mode

# Load the mental health dataset (train section)
dataset = load_dataset("Amod/mental_health_counseling_conversations")["train"]

class TherapyDataset(Dataset):
    def __init__(self, dataset, tokenizer, max_length=512, train=True):
        self.tokenizer = tokenizer
        self.max_length = max_length
        dataset = dataset.to_dict()
        split = int(len(dataset["Context"]) * 0.9)

        if train:
            self.dataset = {
                'Context': dataset["Context"][:split],
                'Response': dataset["Response"][:split]
            }
        else:
            self.dataset = {
                'Context': dataset["Context"][split:],
                'Response': dataset["Response"][split:]
            }

        # Set pad token if not already set
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def __len__(self):
        return len(self.dataset['Context'])
    def __getitem__(self, idx):
        # Separate patient context and therapist response
        patient_context = self.dataset['Context'][idx]
        therapist_response = self.dataset['Response'][idx]

        # Tokenize with return_tensors=None to get lists instead of tensors
        patient_encodings = self.tokenizer(
            patient_context,
            truncation=True,
            max_length=self.max_length,
            padding='max_length',
            return_tensors=None
        )

        therapist_encodings = self.tokenizer(
            therapist_response,
            truncation=True,
            max_length=self.max_length,
            padding='max_length',
            return_tensors=None
        )

        # Careful tensor conversion with explicit dtype
        input_ids = torch.tensor(patient_encodings['input_ids'], dtype=torch.long)
        attention_mask = torch.tensor(patient_encodings['attention_mask'], dtype=torch.long)

        # Create labels with careful conversion
        labels = torch.tensor(therapist_encodings['input_ids'], dtype=torch.long)

        # Replace pad tokens with -100 for loss computation
        labels[labels == self.tokenizer.pad_token_id] = -100

        return {
            'input_ids': input_ids,
            'attention_mask': attention_mask,
            'labels': labels
        }

# Create train and test datasets using the class
train_dataset = TherapyDataset(dataset, chatbot_tokenizer, train=True)
test_dataset = TherapyDataset(dataset, chatbot_tokenizer, train=False)

# Print patient context
print("Patient Context:")
print(chatbot_tokenizer.decode(train_dataset[3]['input_ids'].tolist(), skip_special_tokens=True))

# Print therapist response
print("Therapist Response:")
# Filter out -100 values before decoding
valid_labels = train_dataset[3]['labels']
valid_labels = valid_labels[valid_labels != -100]
print(chatbot_tokenizer.decode(valid_labels.tolist(), skip_special_tokens=True))

# Load batches
batch_size = 8
train_loader = torch.utils.data.DataLoader(
    train_dataset,
    batch_size=batch_size,
    shuffle=True
)
test_loader = torch.utils.data.DataLoader(
    test_dataset,
    batch_size=batch_size,
    shuffle=True
)

# Unfreeze some layers
for param in model.parameters():
    param.requires_grad = False
for param in model.model.decoder.embed_tokens.parameters():
    param.requires_grad = True
for param in model.model.decoder.embed_positions.parameters():
    param.requires_grad = True

for layer in model.model.decoder.layers[-4:]:
    for param in layer.parameters():
        param.requires_grad = True

for param in model.lm_head.parameters():
    param.requires_grad = True

# Define the optimizer
optimizer = torch.optim.Adam(model.parameters(), lr=1e-5, weight_decay=0.01)

# Training loop
def batch_gd(model, optimizer, train_loader, test_loader, epochs, device):
    train_losses = np.zeros(epochs)
    test_losses = np.zeros(epochs)
    for it in range(epochs):
        t0 = datetime.now()
        train_loss = []
        model.train()
        for batch in train_loader:
            # Get batch data
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['labels'].to(device)

