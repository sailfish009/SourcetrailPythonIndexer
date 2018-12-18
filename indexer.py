import jedi
import json
import os
import sourcetraildb as srctrl


def indexSourceCode(sourceCode, workingDirectory, astVisitorClient, isVerbose):
	sourceFilePath = "virtual_file.py"

	environment = None

	project = jedi.api.project.Project(workingDirectory)

	evaluator = jedi.evaluate.Evaluator(
		project, 
		environment=environment,
		script_path=workingDirectory
	)

	module_node = evaluator.parse(
		code=sourceCode,
		path=workingDirectory,
		cache=False,
		diff_cache=False
	)

	if (isVerbose):
		astVisitor = VerboseAstVisitor(astVisitorClient, sourceFilePath, sourceCode) 
	else:
		astVisitor = AstVisitor(astVisitorClient, sourceFilePath, sourceCode) 

	astVisitor.traverseNode(module_node)


def indexSourceFile(sourceFilePath, workingDirectory, astVisitorClient, isVerbose):
	sourceCode = ""
	with open(sourceFilePath, 'r') as input:
		sourceCode=input.read()

	environment = None

	project = jedi.api.project.Project(workingDirectory)

	evaluator = jedi.evaluate.Evaluator(
		project, 
		environment=environment,
		script_path=workingDirectory
	)

	module_node = evaluator.parse(
		code=sourceCode,
		path=workingDirectory,
		cache=False,
		diff_cache=False
	)

	if (isVerbose):
		astVisitor = VerboseAstVisitor(astVisitorClient, sourceFilePath) 
	else:
		astVisitor = AstVisitor(astVisitorClient, sourceFilePath) 

	astVisitor.traverseNode(module_node)


class AstVisitor:

	sourceFilePath = None
	sourceFileContent = None
	client = None
	contextSymbolIdStack = []


	def __init__(self, client, sourceFilePath, sourceFileContent = None):
		self.client = client
		self.sourceFilePath = sourceFilePath
		self.sourceFileContent = sourceFileContent
		fileId = self.client.recordFile(sourceFilePath.replace("\\", "/"))
		if not fileId:
			print("ERROR: " + srctrl.getLastError())
		self.contextSymbolIdStack.append(fileId)
		self.client.recordFileLanguage(fileId, "python")


	def beginVisitName(self, node):
		if self.contextSymbolIdStack:
			(startLine, startColumn) = node.start_pos
			if self.sourceFileContent is None: # we are indexing a real file
				script = jedi.Script(None, startLine, startColumn, self.sourceFilePath)
			else: # we are indexing a provided code snippet
				script = jedi.Script(self.sourceFileContent, startLine, startColumn)
			for definition in script.goto_definitions():
				if not definition.line or not definition.column:
					# Early exit. For now we don't record references for names that don't have a valid definition location 
					return

				if definition.line == startLine and definition.column == startColumn:
					# Early exit. We don't record references for locations of names that are definitions
					return

				if definition is not None and definition._name is not None and definition._name.tree_name is not None:
					referencedNameHierarchy = getNameHierarchyOfNode(definition._name.tree_name)

					referencedSymbolId = self.client.recordSymbol(referencedNameHierarchy)
					contextSymbolId = self.contextSymbolIdStack[len(self.contextSymbolIdStack) - 1]
					
					referenceId = self.client.recordReference(
						contextSymbolId,
						referencedSymbolId, 
						srctrl.REFERENCE_CALL
					)
					if not referenceId:
						print("ERROR: " + srctrl.getLastError())
					
					self.client.recordReferenceLocation(referenceId, getParseLocationOfNode(node))
					break # we just record usage of the first definition


	def endVisitName(self, node):
		pass


	def beginVisitClassdef(self, node):
		nameNode = getDirectChildWithType(node, 'name')
		symbolId = self.client.recordSymbol(getNameHierarchyOfNode(node))
		self.client.recordSymbolDefinitionKind(symbolId, srctrl.DEFINITION_EXPLICIT)
		self.client.recordSymbolKind(symbolId, srctrl.SYMBOL_CLASS)
		self.client.recordSymbolLocation(symbolId, getParseLocationOfNode(nameNode))
		self.client.recordSymbolScopeLocation(symbolId, getParseLocationOfNode(node))
		self.contextSymbolIdStack.append(symbolId)


	def endVisitClassdef(self, node):
		self.contextSymbolIdStack.pop()


	def beginVisitExprStmt(self, node):
		parentClassdefNode = getParentWithType(node, 'classdef')
		if not parentClassdefNode == None:
			definedNames = node.get_defined_names()
			for nameNode in definedNames:
				symbolId = self.client.recordSymbol(getNameHierarchyOfNode(nameNode))
				self.client.recordSymbolDefinitionKind(symbolId, srctrl.DEFINITION_EXPLICIT)
				self.client.recordSymbolKind(symbolId, srctrl.SYMBOL_FIELD)
				self.client.recordSymbolLocation(symbolId, getParseLocationOfNode(nameNode))


	def endVisitExprStmt(self, node):
		pass


	def beginVisitFuncdef(self, node):
		nameNode = getDirectChildWithType(node, 'name')
		symbolId = self.client.recordSymbol(getNameHierarchyOfNode(node))
		self.client.recordSymbolDefinitionKind(symbolId, srctrl.DEFINITION_EXPLICIT)
		self.client.recordSymbolKind(symbolId, srctrl.SYMBOL_FUNCTION)
		self.client.recordSymbolLocation(symbolId, getParseLocationOfNode(nameNode))
		self.client.recordSymbolScopeLocation(symbolId, getParseLocationOfNode(node))
		self.contextSymbolIdStack.append(symbolId)


	def endVisitFuncdef(self, node):
		self.contextSymbolIdStack.pop()


	def traverseNode(self, node):
		if not node:
			return
		
		if node.type == 'name':
			self.beginVisitName(node)
		elif node.type == 'classdef':
			self.beginVisitClassdef(node)
		elif node.type == 'expr_stmt':
			self.beginVisitExprStmt(node)
		elif node.type == 'funcdef':
			self.beginVisitFuncdef(node)
		
		if hasattr(node, 'children'):
			for c in node.children:
				self.traverseNode(c)

		if node.type == 'name':
			self.endVisitName(node)
		elif node.type == 'classdef':
			self.endVisitClassdef(node)
		elif node.type == 'expr_stmt':
			self.endVisitExprStmt(node)
		elif node.type == 'funcdef':
			self.endVisitFuncdef(node)


class VerboseAstVisitor(AstVisitor):

	def __init__(self, client, sourceFilePath, sourceFileContent = None):
		AstVisitor.__init__(self, client, sourceFilePath, sourceFileContent)
		self.indentationLevel = 0
		self.indentationToken = "| "


	def traverseNode(self, node):
		currentIndentation = ""
		for i in range(0, self.indentationLevel):
			currentIndentation += self.indentationToken

		print("AST: " + currentIndentation + node.type)

		self.indentationLevel += 1
		AstVisitor.traverseNode(self, node)
		self.indentationLevel -= 1


class AstVisitorClient:

	indexedFileId = 0

	def __init__(self):
		if srctrl.isCompatible():
			print("Loaded database is compatible.")
		else:
			print("Loaded database is not compatible.")
			print("Supported DB Version: " + str(srctrl.getSupportedDatabaseVersion()))
			print("Loaded DB Version: " + str(srctrl.getLoadedDatabaseVersion()))


	def recordSymbol(self, nameHierarchy):
		symbolId = srctrl.recordSymbol(nameHierarchy.serialize())
		return symbolId


	def recordSymbolDefinitionKind(self, symbolId, symbolDefinitionKind):
		srctrl.recordSymbolDefinitionKind(symbolId, symbolDefinitionKind)


	def recordSymbolKind(self, symbolId, symbolKind):
		srctrl.recordSymbolKind(symbolId, symbolKind)


	def recordSymbolLocation(self, symbolId, parseLocation):
		srctrl.recordSymbolLocation(
			symbolId, 
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


	def recordSymbolScopeLocation(self, symbolId, parseLocation):
		srctrl.recordSymbolScopeLocation(
			symbolId, 
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


	def recordSymbolSignatureLocation(self, symbolId, parseLocation):
		srctrl.recordSymbolSignatureLocation(
			symbolId, 
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


	def recordReference(self, contextSymbolId, referencedSymbolId, referenceKind):
		return srctrl.recordReference(
			contextSymbolId,
			referencedSymbolId, 
			referenceKind
		)


	def recordReferenceLocation(self, referenceId, parseLocation):
		srctrl.recordReferenceLocation(
			referenceId, 
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


	def recordFile(self, filePath):
		self.indexedFileId = srctrl.recordFile(filePath)
		srctrl.recordFileLanguage(self.indexedFileId, "python")
		return self.indexedFileId


	def recordFileLanguage(self, fileId, languageIdentifier):
		srctrl.recordFileLanguage(fileId, languageIdentifier)


	def recordLocalSymbol(self, name):
		return srctrl.recordLocalSymbol(name)


	def recordLocalSymbolLocation(self, localSymbolId, parseLocation):
		srctrl.recordLocalSymbolLocation(
			localSymbolId, 
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


	def recordCommentLocation(self, parseLocation):
		srctrl.recordCommentLocation(
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


	def recordError(self, message, fatal, parseLocation):
		srctrl.recordError(
			message,
			fatal, 
			self.indexedFileId, 
			parseLocation.startLine, 
			parseLocation.startColumn, 
			parseLocation.endLine, 
			parseLocation.endColumn
		)


class ParseLocation:

	def __init__(self, startLine, startColumn, endLine, endColumn):
		self.startLine = startLine
		self.startColumn = startColumn
		self.endLine = endLine
		self.endColumn = endColumn


	def toString(self):
		return "[" + str(self.startLine) + ":" + str(self.startColumn) + "|" + str(self.endLine) + ":" + str(self.endColumn) + "]"


class NameHierarchy():

	delimiter = ""


	def __init__(self, nameElement, delimiter):
		self.nameElements = []
		if not nameElement == None:
			self.nameElements.append(nameElement)
		self.delimiter = delimiter

	def serialize(self):
		return json.dumps(self, cls=NameHierarchyEncoder)

	def getDisplayString(self):
		displayString = ""
		isFirst = True
		for nameElement in self.nameElements:
			if not isFirst:
				displayString += self.delimiter
			isFirst = False
			if len(nameElement.prefix) > 0:
				displayString += nameElement.prefix + " " 
			displayString += nameElement.name
			if len(nameElement.postfix) > 0:
				displayString += nameElement.postfix 
		return displayString


class NameElement:

	name = ""
	prefix = ""
	postfix = ""


	def __init__(self, name, prefix = "", postfix = ""):
		self.name = name
		self.prefix = prefix
		self.postfix = postfix


class NameHierarchyEncoder(json.JSONEncoder):
	
	def default(self, obj):
		if isinstance(obj, NameHierarchy):
			return {
				"name_delimiter": obj.delimiter,
				"name_elements": [nameElement.__dict__ for nameElement in obj.nameElements]
			}
		# Let the base class default method raise the TypeError
		return json.JSONEncoder.default(self, obj)


def getNameHierarchyOfNode(node):
	if node == None:
		return None
	nameNode = None
	if node.type == 'name':
		nameNode = node
	else:
		nameNode = getDirectChildWithType(node, 'name')
	if nameNode == None:
		return None
	nameElement = NameElement(nameNode.value)
	parentNode = node.parent
	if node.type == 'name':
		if not parentNode == None:
			parentNode = parentNode.parent
	while not parentNode == None:
		parentNodeNameHierarchy = getNameHierarchyOfNode(parentNode)
		if not parentNodeNameHierarchy == None:
			parentNodeNameHierarchy.nameElements.append(nameElement)
			return parentNodeNameHierarchy
		parentNode = parentNode.parent
	return NameHierarchy(nameElement, ".")


def getParseLocationOfNode(node):
	startLine, startColumn = node.start_pos
	endLine, endColumn = node.end_pos
	return ParseLocation(startLine, startColumn + 1, endLine, endColumn)


def getParentWithType(node, type):
	if node == None:
		return None
	parentNode = node.parent
	if parentNode == None:
		return None
	if parentNode.type == type:
		return parentNode
	return getParentWithType(parentNode, type)
	


def getDirectChildWithType(node, type):
	for c in node.children:
		if c.type == type:
			return c
	return None