'''
Concrete MethodModule class for a specific learning MethodModule
'''

# Copyright (c) 2017-Current Jiawei Zhang <jiawei@ifmlab.org>
# License: TBD

from code.base_class.method import method
from Evaluate_Accuracy import Evaluate_Accuracy
import torch
from torch import nn
import numpy as np
from collections import OrderedDict
from torchinfo import summary
from torch.utils.tensorboard import SummaryWriter
from transformers import BertModel
from tqdm import trange


BERTMAX_LEN = 512
class Method_Classification(method, nn.Module):
    data = {
        'sLossFunction': 'CrossEntropy',
        'sOptimizer': 'ADAM',
        'sInputSize': 768, # BERT
        'sHiddenSize': 512,
        'sGradClipAmt': 0.5,
        'sDropout': 0.5,
        'sOutputDim': 2,    # Binary classification
        'sLearningRate': 1e-4,
        'sMomentum': 0.9,
        'sMaxEpoch': 5,  # ! CHANGE LATER
        'sBatchSize': 100,  # Lower than 4000 is required
        'sRandSeed': 47
    }

    def __init__(self, mName='', mDescription='', sData=None):

        method.__init__(self, mName, mDescription)
        nn.Module.__init__(self)

        if sData:
            for k, v in sData.items():
                self.data[k] = v

        self.model_res_name = 'rnn_models'

        for k, v in self.data.items():
            if k != 'train' and k != 'test':
                self.model_res_name += f'_{k}:{v}'

        self.writer = SummaryWriter(
            comment=self.model_res_name)
        
        self.inputLayer = OrderedDict([
            ('rnn_layer_1', nn.RNN(input_size=self.data['sInputSize'], hidden_size=self.data['sHiddenSize'], batch_first=True))
        ])
        self.outputLayer = OrderedDict([
            ('flatten_layer_2', nn.Flatten()),
            ('linear_layer_2', nn.Linear(in_features=262144, out_features=self.data['sOutputDim'])) # ! Potentially wrong size, change the input later
        ])

        # do not use softmax if we have nn.CrossEntropyLoss base on the PyTorch documents
        # ? https://stackoverflow.com/questions/55675345/should-i-use-softmax-as-output-when-using-cross-entropy-loss-in-pytorch
        if self.data['sLossFunction'] != 'CrossEntropy':
            self.outputLayer['output'] = nn.Softmax(dim=1)
        else:
            self.outputLayer['output'] = nn.Sigmoid()

        # Compile all layers
        self.layers = nn.ModuleDict(self.compileLayers())

        if self.data['sLossFunction'] == 'MSE':
            self.lossFunction = nn.MSELoss()
        else:
            self.lossFunction = nn.CrossEntropyLoss()

        if self.data['sOptimizer'] == 'SGD':
            self.optimizer = torch.optim.SGD(self.parameters(),
                                             lr=self.data['sLearningRate'],
                                             momentum=self.data['sMomentum'])
        else:
            self.optimizer = torch.optim.Adam(self.parameters(),
                                              lr=self.data['sLearningRate'])
        self.lossList = []  # for plotting loss

        # Load the BERT embedding model
        self.bertModel = BertModel.from_pretrained('bert-base-uncased', output_hidden_states=True).cuda()
        self.bertModel.eval()    # Only wants to use the bert model

        self.cuda()

    def compileLayers(self) -> OrderedDict:
        res = OrderedDict()
        res.update(self.inputLayer)
        res.update(self.outputLayer)
        return res

    def forward(self, x):
        '''Forward propagation'''
        out = x
        for name, func in self.layers.items():
            # print(f'{name} - {out.shape}')
            if 'rnn' in name:
                out = func(out)[0]  # we dont use the hidden states from rnn
            else:
                out = func(out)
        return out

    # backward error propagation will be implemented by pytorch automatically
    # so we don't need to define the error backpropagation function here

    '''
    @param: embedding the batches into list of entry of each tensor
    '''
    # MAKE SURE TO STACK THEM TO BATCHES
    def embeddingBatchToEntry(self, batchX) -> list:
        #! Pass through BERT use multiple batches for better efficiency
        # for i, each in enumerate(tqdm(inputData['train']['X'], desc="Embedding Train data on BERT")):
        embeddedBatches = []
        with torch.no_grad():
            tokenList = []
            tokenTypeList = []
            attentionList = []

            for entry in batchX:
            # Convert inputs to pytorch tensor
                tokenList.append(entry['input_ids'])
                tokenTypeList.append(entry['token_type_ids'])
                attentionList.append(entry['attention_mask'])

            tokenTensor = torch.tensor(tokenList).cuda()
            tokenTypeTensor = torch.tensor(tokenTypeList).cuda()
            attentionTensor = torch.tensor(attentionList).cuda()

            # Need to be in shape (batch_size, sequeunce_length)
            # print(f'Shape of the tensor: {tokenTensor.shape} and {attentionTensor.shape}')

            output = self.bertModel(input_ids=tokenTensor, attention_mask=attentionTensor, token_type_ids=tokenTypeTensor)
            # print('Got through embedding in EMBEDDING FUNC')
            last_hidden_statesBatches = output[0]

            for batch in last_hidden_statesBatches:
                embeddedBatches.append(batch)
        return embeddedBatches



    def trainModel(self, X, y):
        # #!!!! Debugging 
        # torch.autograd.set_detect_anomaly(True)

        # Turn on train mode for all layers and prepare data
        self.training = True
        optimizer = self.optimizer
        loss_function = self.lossFunction

        accuracy_evaluator = Evaluate_Accuracy('training evaluator', '')


        for epoch in trange(self.data['sMaxEpoch'], desc='Training epochs'):
            permutation = torch.randperm(len(X))    # random order of batches
            for i in trange(0, len(X), self.data['sBatchSize'], desc=f'Batch progression at epoch {epoch}'):
                indices = permutation[i:i+self.data['sBatchSize']]
                batchX, batchY = [X[i] for i in indices], [y[i] for i in indices]   # batches

                # batchedEmbedding = self.embeddingBatchToEntry(batchX)
                batchXTensor = torch.stack(self.embeddingBatchToEntry(batchX))
                # print(f'Len of the batch Tensor: {len(batchXTensor)}')

                # ! Begin forward here
                fold_pred = self.forward(batchXTensor.cuda())
                fold_true = torch.LongTensor(np.array(batchY)).cuda()

                # print(f'len of the target batch: {fold_true.shape}')
                # print(f'len of the predicted batch: {fold_pred.shape}')
                
                # calculate the training loss
                train_loss = loss_function(fold_pred, fold_true)
                optimizer.zero_grad()
                train_loss.backward()

                # TODO: Potential modification
                # `clip_grad_norm` helps prevent the exploding gradient problem in RNNs / LSTMs.
                torch.nn.utils.clip_grad_norm_(self.parameters(), self.data['sGradClipAmt'])

                optimizer.step()

            # The y_pred.max(1)[1] return the indices of max value on each row of a tensor (the y_pred is a tensor)
            accuracy_evaluator.data = {
                'true_y': fold_true.cpu(), 'pred_y': fold_pred.cpu().max(dim=1)[1]}
            # accuracy_evaluator.data = {'true_y': y_true, 'pred_y': y_pred.max(dim=1)}
            acc = accuracy_evaluator.evaluate()
            loss = train_loss.item()
            print('Epoch:', epoch, 'Accuracy:',
                    acc, 'Loss:', loss)

            # Record data for ploting
            self.lossList.append(loss)

            # Record data for tensorboard
            self.writer.add_scalar('Training Loss', train_loss, epoch)
            self.writer.add_scalar('Accuracy', acc, epoch)

            # Check learning progress
            for name, weight in self.named_parameters():
                if 'bertModel' not in name:
                    self.writer.add_histogram(name, weight, epoch)
                    self.writer.add_histogram(f'{name}.grad', weight.grad, epoch)

    def test(self, X):
        # Set to test mode
        y_predTotal = []
        self.training = False
        with torch.no_grad():
            for i in trange(0, len(X), self.data['sBatchSize'], desc='Test data batch progress'):
                batchX = X[i:i+self.data['sBatchSize']]
                embeddedTestBatch = self.embeddingBatchToEntry(batchX)
                inTensor = torch.stack(embeddedTestBatch).cuda()
                y_predTotal.extend(self.forward(inTensor).cpu().max(dim=1)[1])

        return y_predTotal

    def run(self):
        #! Visualize the architecture
        # unsqueeze to have 1 as batch number dimension
        # print(f"Data type is: {type(self.data['train']['X'][0])}")
        # print(f"Value is: {self.data['train']['X'][0]}")
        # print(f"INSIDE run: {self.data['train']['X'][0:3]}")
        inputBatch = torch.stack(self.embeddingBatchToEntry(self.data['train']['X'][0:self.data['sBatchSize']])).cuda()
        # print(f'value: {inputBatch}')
        # print(f'Shape of input: {inputBatch.shape}')

        self.writer.add_graph(self, inputBatch)

        #! Actual run
        print('method running...')
        print('--network status--')
        summary(self, 
                input_size=(self.data['sBatchSize'], 512, 768)
                )
        print('--start training...')
        self.trainModel(self.data['train']['X'], self.data['train']['y'])
        print('--start testing...')
        pred_y = self.test(self.data['test']['X'])

        # print(f'{pred_y} and the length {len(pred_y)} also length of each')
        # ALso for tensor
        return {'pred_y': pred_y, 'true_y': self.data['test']['y'],
                'loss': self.lossList}
