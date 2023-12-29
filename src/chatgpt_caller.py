import numpy as np
import openai
import json
import re
import hashlib
import os.path
import pandas as pd
from sklearn.cluster import KMeans
from rich.progress import track
import time
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.chat_models import ChatOpenAI
from langchain.schema.messages import HumanMessage, SystemMessage

class chatgpt_caller():
    def __init__(self, folder, api_key):
        self.api_key = api_key
        
        # openai.api_key = self.api_key
        openai.api_key = api_key
        openai.api_base = "http://localhost:8000/v1"
        self.embedding_model = OpenAIEmbeddings(model="text-embedding-ada-002", openai_api_key="EMPTY", openai_api_base="http://localhost:8000/v1")
        self.chatllm = ChatOpenAI(model="gpt-3.5-turbo", openai_api_key="EMPTY", openai_api_base="http://localhost:8000/v1")
        self.COMPLETIONS_MODEL = "gpt-3.5-turbo"
        self.EMBEDDING_MODEL = "text-embedding-ada-002"
        self.CONTEXT_TOKEN_LIMIT = 1500
        self.TOKENS_PER_TOPIC = 2000
        self.TOPIC_NUM_MIN = 3
        self.TOPIC_NUM_MAX = 10

        self.content = ""
        self.embeddings = []
        self.sources = []

        self.folder = folder
        self.create_folder(folder)

    def create_folder(self, folder_path):
        # create path if not exists
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

    def get_embedding(self, text):
        if text == "" or text is None:
            return
        embed_folder = os.path.join(self.folder, 'embeddings/cache/')
        self.create_folder(embed_folder)
        tmpfile = embed_folder+hashlib.md5(text.encode('utf-8')).hexdigest()+".json"
        if os.path.isfile(tmpfile):
            with open(tmpfile , 'r', encoding='UTF-8') as f:
                return json.load(f)

        # TODO: add batch embeding https://api.python.langchain.com/en/latest/embeddings/langchain.embeddings.openai.OpenAIEmbeddings.html
        result = self.embedding_model.embed_query(text)

        with open(tmpfile, 'w',encoding='utf-8') as handle2:
            json.dump(result, handle2, ensure_ascii=False, indent=4) # type: ignore
        
        return result # type: ignore

    def get_embedding_old(self, text):
        embed_folder = os.path.join(self.folder, 'embeddings/cache/')
        self.create_folder(embed_folder)
        tmpfile = embed_folder+hashlib.md5(text.encode('utf-8')).hexdigest()+".json"
        if os.path.isfile(tmpfile):
            with open(tmpfile , 'r', encoding='UTF-8') as f:
                return json.load(f)
        print(text)
        result = self.openai_client.embeddings.create(
            model=self.EMBEDDING_MODEL,
            input=text
        )

        with open(tmpfile, 'w',encoding='utf-8') as handle2:
            json.dump(result["data"][0]["embedding"], handle2, ensure_ascii=False, indent=4) # type: ignore
        
        return result["data"][0]["embedding"] # type: ignore
        
    def get_topic_num(self):
        num = int(len("".join(self.sources))/self.TOKENS_PER_TOPIC)
        if num < self.TOPIC_NUM_MIN:
            return self.TOPIC_NUM_MIN
        if num > self.TOPIC_NUM_MAX: 
            return self.TOPIC_NUM_MAX
        return num
        
    def get3questions(self):
        matrix = np.vstack(self.embeddings)
    
        df = pd.DataFrame({"embedding":self.embeddings,"p":self.sources})
        n_clusters = self.get_topic_num()
        kmeans = KMeans(n_clusters=n_clusters, init="k-means++", random_state=42)
        kmeans.fit(matrix)
        df["Cluster"] = kmeans.labels_  

        df2 = {"tokens":[], "prompts":[]}
        for i in range(n_clusters):
            ps = df[df.Cluster == i]["p"].values
            ctx = "\n".join(ps)[:self.CONTEXT_TOKEN_LIMIT]
            prompt = f"Suggest a simple, clear, single, short question base on the context, answer in the same language of context\n\nContext:\n{ctx}\n\nAnswer with the language used in context, the question is:"
            df2["tokens"].append(len(" ".join(ps)))
            df2["prompts"].append(prompt)
        df2 = pd.DataFrame(df2)
        
        # print("######questions#######")
        questions = []
        for prompt in df2.prompts.sample(3).values:
            # print(prompt)
            try:
                completion = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content":prompt}], api_key=self.api_key)
                question = completion.choices[0].message.content # type: ignore
                questions.append(question)
                print(question)
            except Exception as e:
                print("Error when deal with questions: ", e)
            # print("***********************")
        return questions

    def file2embedding(self, text):
        self.content = text

        self.sources = self.content.split('\n\n')
        temp_sources = []
        for i, source in track(enumerate(self.sources)):
            if source.strip() == '':
                continue
            embed = self.get_embedding(source)
            if embed is not None:
                self.embeddings.append(embed)
                temp_sources.append(source)
            
        self.sources = temp_sources

        self.questions = self.get3questions()
    
        with open(os.path.join(self.folder, "embed_result.json"), 'w',encoding='utf-8') as f:
            json.dump({"sources": self.sources, "embeddings": self.embeddings, "questions": self.questions}, f, ensure_ascii=False, indent=4)
    
    def vector_similarity(self, x, y):
        """
        Returns the similarity between two vectors.
        
        Because OpenAI Embeddings are normalized to length 1, the cosine similarity is the same as the dot product.
        """
        return np.dot(np.array(x), np.array(y))
            
    def order_document_sections_by_query_similarity(self, query):
        """
        Find the query embedding for the supplied query, and compare it against all of the pre-calculated document embeddings
        to find the most relevant sections. 
        
        Return the list of document sections, sorted by relevance in descending order.
        """
        query_embedding = self.get_embedding(query)
        
        document_similarities = sorted([
            (self.vector_similarity(query_embedding, doc_embedding), doc_index) for doc_index, doc_embedding in enumerate(self.embeddings)
        ], reverse=True, key=lambda x: x[0])
        
        return document_similarities
            
    def ask(self, question):
        ordered_candidates = self.order_document_sections_by_query_similarity(question)
        ctx = u""
        for candi in ordered_candidates:
            next = ctx + u"\n" + self.sources[candi[1]]
            if len(next)>self.CONTEXT_TOKEN_LIMIT:
                break
            ctx = next
        if len(ctx) == 0:
          return u""    
        
        prompt = u"".join([
            u"Answer the question base on the context, answer in the same language of question\n\n"
            u"Context:" + ctx + u"\n\n"
            u"Question:" + question + u"\n\n"
        ])

        completion = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content":prompt}])
        return [prompt, completion.choices[0].message.content]
    
    def chat(self, prompt):
        messages = [
            SystemMessage(content="You're a helpful paper reading assistant"),
            HumanMessage(content=prompt),
        ]

        response = self.chatllm.invoke(messages)
        return response

#     def choose(self, stem, choices, choices_explain = None, num_tries = 3, verbose = False):
#         error_msg = ''
#         if choices_explain is not None:
#             assert(len(choices_explain)==len(choices))
#         for i in range(num_tries):
#             system_prompt = '''You are a chooser returning indexes of choices according to the stem.\nYou are to output the indexes in the format: ###number,number,number###. For example:
# The stem: choose from the choices.
# Choices:
# 0 @@cat@@
# Explaination of the choice: This is a cat
# 1 @@dog@@
# Explaination of the choice: This is a dog
# 2 @@apple@@
# Explaination of the choice: This is a apple
# 3 @@banana@@
# Explaination of the choice: This is a banana
# Answer: ###0,3###
# Just Return the Answer and Don't Explain the Answer!!!
# '''
#             user_prompt = f"The stem: {stem}\nChoices:\n"
#             for index, choice in enumerate(choices):
#                 user_prompt += str(index)
#                 user_prompt += f"  @@{choice}@@\n"
#                 if choices_explain is not None:
#                     user_prompt += f"Explaination of the choice: {choices_explain[index]}\n"
#             user_prompt += "Answer: "
#             response = self.chatllm.invoke(
#                 [
#                     SystemMessage(content = system_prompt + error_msg),
#                     HumanMessage(content = user_prompt),
#                 ]
#             )
#             res = response.content

#             if verbose:
#                 print('System prompt:', system_prompt + error_msg)
#                 print('\nUser prompt:', user_prompt)
#                 print('\nGPT response:', res)

#             try:
#                 pattern = r"###(.*?)###"
#                 matches = re.findall(pattern, res)

#                 if len(matches)==0:
#                     raise Exception(f"Answer not in correct format")
                
#                 nums_string = matches[0].split(",")
#                 try:
#                 ## TODO: check number of answers
#                     nums = [int(i) for i in nums_string]
#                 except Exception:
#                     raise Exception(f"The answer should be index numbers(int) in the correct format but not the choices.")
                
#                 return nums
#             except Exception as e:
#                 error_msg = f"\n\nAnswer: {res}\n\nError message: {str(e)}\nYou must use \"###number,number###\" to enclose the answer."
#                 if verbose:
#                     print("An exception occurred:", str(e))
#                     print("Current invalid format:", res)

#         return []
    
    def choose(self, stem, choices, choices_explain = None, num_tries = 3, verbose = False):
        error_msg = ''
        if choices_explain is not None:
            assert(len(choices_explain)==len(choices))
        for i in range(num_tries):
            system_prompt = '''You are a chooser returning indexes of choices according to the stem.\nYou are to output the indexes in the format: ###number,number,number###. For example:
The stem: choose from the choices.
Choices:
0 @@cat@@
Explaination of the choice: This is a cat
1 @@dog@@
Explaination of the choice: This is a dog
2 @@apple@@
Explaination of the choice: This is a apple
3 @@banana@@
Explaination of the choice: This is a banana
Your Answer: ###0,3###
Just Return the Answer and Don't Explain the Answer!!!
'''
            user_prompt = f"The stem: {stem}\nChoices:\n"
            for index, choice in enumerate(choices):
                user_prompt += str(index)
                user_prompt += f"  @@{choice}@@\n"
                if choices_explain is not None:
                    user_prompt += f"Explaination of the choice: {choices_explain[index]}\n"
            user_prompt += "Return Your Answer: "
            response = self.chatllm.invoke(
                [
                    SystemMessage(content = system_prompt + error_msg),
                    HumanMessage(content = user_prompt),
                ]
            )
            res = response.content

            if verbose:
                print('System prompt:', system_prompt)
                print('\nUser prompt:', user_prompt + error_msg)
                print('\nGPT response:', res)

            try:
                pattern = r"###(.*?)###"
                matches = re.findall(pattern, res)

                if len(matches)==0:
                    raise Exception(f"Answer not in correct format")
                
                nums_string = matches[0].split(",")
                try:
                ## TODO: check number of answers
                    nums = [int(i) for i in nums_string]
                except Exception:
                    raise Exception(f"The answer should be index numbers(int) in the correct format but not the choices.")
                
                return nums
            except Exception as e:
                error_msg = f"\n\nWarning: Your previous Answer: {res}\n\nError message: {str(e)}\nYou must use \"###number,number###\" to enclose the answer."
                if verbose:
                    print("An exception occurred:", str(e))
                    print("Current invalid format:", res)

        return []
    
    def choose(self, stem, choices, choices_explain=None, num_tries=3, verbose=False):
        error_msg = ''
        if choices_explain is not None:
            assert(len(choices_explain) == len(choices))

        for i in range(num_tries):
            system_prompt = "You are a chooser returning indexes of choices according to the stem.\n \
You are to output the indexes in the format: {\"indexes\": [number, number, number]}.\n \
For example:\n \
The stem: choose from the choices.\n \
Choices:\n \
0 @@cat@@\n \
Explaination of the choice: This is a cat\n \
1 @@dog@@\n \
Explaination of the choice: This is a dog\n \
2 @@apple@@\n \
Explaination of the choice: This is an apple\n \
3 @@banana@@\n \
Explaination of the choice: This is a banana\n \
Your Answer: {\"indexes\": [0, 3]}\n \
Just Return the Answer and Don't Explain the Answer!!!\n"
            user_prompt = f"The stem: {stem}\nChoices:\n"

            for index, choice in enumerate(choices):
                user_prompt += str(index)
                user_prompt += f"  @@{choice}@@\n"
                if choices_explain is not None:
                    user_prompt += f"Explanation of the choice: {choices_explain[index]}\n"

            user_prompt += "Your Answer: "
            response = self.chatllm.invoke(
                [
                    SystemMessage(content=system_prompt + error_msg),
                    HumanMessage(content=user_prompt),
                ]
            )
            res = response.content

            if verbose:
                print('System prompt:', system_prompt)
                print('\nUser prompt:', user_prompt + error_msg)
                print('\nGPT response:', res)

            try:
                # 使用正则表达式解析 JSON 格式的答案
                pattern = r'{"indexes": \[([0-9, ]+)\]}'
                matches = re.search(pattern, res)

                if not matches:
                    raise Exception("Answer not in the correct format")

                nums_string = matches.group(1).split(", ")
                nums = [int(i) for i in nums_string]

                return nums

            except Exception as e:
                error_msg = f"\n\nWarning: Your previous Answer: {res}\n\nError message: {str(e)}\nYou must use the format: {{\"indexes\": [number, number, number]}} for the answer."
                if verbose:
                    print("An exception occurred:", str(e))
                    print("Current invalid format:", res)

        return []