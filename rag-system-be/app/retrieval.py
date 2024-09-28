from langdetect import detect
from qdrant_client import QdrantClient
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Qdrant, FAISS
from dotenv import dotenv_values
from langchain_openai import ChatOpenAI
from langchain import hub
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain.memory import ConversationBufferMemory
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

# steps to take
# Convert my user query into an embedding vector, this way we can match what we have in the vector database
# https://python.langchain.com/docs/tutorials/rag/#indexing-load
# https://python.langchain.com/docs/how_to/MultiQueryRetriever/


class LanguageDetector:

    @staticmethod
    def detect_language(query: str) -> str:
        try:
            lang = detect(query)
            if lang in ["ar", "en"]:
                return lang
            raise ValueError(f"Unsupported language: {lang}")
        except Exception as err:
            raise ValueError(f"Error detecting language: {err}")


class EmbeddingModelLoader:

    def __init__(self, api_key: str):
        self.embedding_model = OpenAIEmbeddings(api_key=api_key)

    def get_model(self):
        return self.embedding_model


class VectorStoreManager:

    def __init__(self, qdrant_client: QdrantClient, embedding_model: OpenAIEmbeddings):
        self._en_vectorstore = Qdrant(
            client=qdrant_client, collection_name="en_doc", embeddings=embedding_model)
        self._ar_vectorstore = Qdrant(
            client=qdrant_client, collection_name="ar_doc", embeddings=embedding_model)

    def get_vectorstore(self, language: str) -> Qdrant:
        if language == "en":
            return self._en_vectorstore
        elif language == "ar":
            return self._ar_vectorstore
        else:
            raise ValueError(f"Unsupported language: {language}")


class MemoryManager:

    def __init__(self):
        # Store memory for each user based on user ID
        self.user_memories = {}

    def _get_user_memory(self, user_id: str):
        """Retrieve or initialize a ConversationBufferMemory for the given user_id."""
        if user_id not in self.user_memories:
            self.user_memories[user_id] = ConversationBufferMemory()
        return self.user_memories[user_id]

    def get_chat_history(self, user_id: str) -> str:
        """Retrieve chat history for a specific user."""
        user_memory = self._get_user_memory(user_id)
        memory_variables = user_memory.load_memory_variables({})
        return memory_variables.get('history', '')

    def save_chat_context(self, user_id: str, query: str, response: str):
        """Save the conversation context for a specific user."""
        user_memory = self._get_user_memory(user_id)
        user_memory.save_context({"question": query}, {"answer": response})


class QueryProcessor:

    def __init__(self, embedding_model_loader: EmbeddingModelLoader, vectorstore_manager: VectorStoreManager, memory_manager: MemoryManager):
        self._embedding_model_loader = embedding_model_loader
        self._vectorstore_manager = vectorstore_manager
        self._memory_manager = memory_manager
        self._llm = ChatOpenAI(
            model="gpt-4o-mini", api_key=dotenv_values("../.env").get("OPENAI_API_KEY"), max_tokens=1500)

    @staticmethod
    def _format_docs(docs):
        """Format retrieved documents into a single string"""
        return "\n\n".join(doc.page_content for doc in docs)

    def process_query(self, query: str, userid: int) -> str:
        """Process and query the vector store based on the language and query"""
        try:
            language = LanguageDetector.detect_language(query)
        except:
            return "Please type your query in either Arabic or English, thank you!"
        # Retrieve conversation history from memory
        chat_history = self._memory_manager.get_chat_history(userid)
        full_query = f"{chat_history}\n\nUser: {query}"

        # Select vector store based on the language
        vectorstore = self._vectorstore_manager.get_vectorstore(language)
        retriever = MultiQueryRetriever.from_llm(
            retriever=vectorstore.as_retriever(
                search_type="similarity", search_kwargs={"k": 10}),
            llm=self._llm
        )

        # Load the prompt template from the hub
        prompt = hub.pull("rlm/rag-prompt")

        # Create the RAG chain (Retriever-LLM-Generator)
        rag_chain = (
            # Adjusting to work with the chain
            {"context": retriever | self._format_docs,
                "question": RunnablePassthrough()}
            | prompt
            | self._llm
            | StrOutputParser()
        )

        # Stream the results using the formatted context and user query
        final_response = ""
        for chunk in rag_chain.stream({"question": full_query}):
            print(chunk, end="", flush=True)
            final_response += chunk

        # Save the conversation context in memory
        self._memory_manager.save_chat_context(userid, query, final_response)

        return final_response


if __name__ == "__main__":
    api_key = dotenv_values("../.env").get("OPENAI_API_KEY")
    embedding_loader = EmbeddingModelLoader(api_key)

    # Initialize vector store manager and memory manager
    qdrant_client = QdrantClient("http://localhost:6333")
    vector_manager = VectorStoreManager(
        qdrant_client, embedding_loader.get_model())
    memory_manager = MemoryManager()

    # Initialize Query Processor
    query_processor = QueryProcessor(
        embedding_loader, vector_manager, memory_manager)

    # Example queries
    queries = ["What are the rights of an employee?",
               "What are the implications of leaving a company?"]

    for query in queries:
        query_processor.process_query(query)
