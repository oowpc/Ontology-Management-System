"""ORM 模型公共入口；兼容原有的 from app.models import X。"""
from ..db import Base

from .core import (User, OntologySource, OntologyLibrary, LibraryMember, Concept, ConceptParent, Operator, OperatorInput, ImportBatch, Submission, TmpMapping, BackfillRecord, AuditLog, UsageStat, HealthReport, KeUpload, KeTask)
from .gold import (GoldArticle, GoldScheduleCell, GoldIssue, GoldIssueComment, GoldSample, GoldSampleItem, GoldSampleReview, GoldItem, ApiToken, KeReview, KeolEntity, KeolFact, LlmCall)
from .questions import (Question, QuestionConcept, QuestionKeVersion, QuestionIssue)
from .issues import (Issue, IssueCase, IssueQuestion, IssueViewpoint, IssueComment, IssueLink, IssueImpact)
from .annotation import (AnnotationBatch, AnnotationMember, AnnotationUnit, AnnotationEvidence, CoAnnotation, CoAlignment, KeAnnotation, GoldKe, OntologyVersion, OntologySuggestion, CoMergeLog, AnnotationCrossReview)
from .integrations import (Nl2keRun, DatamakerJob, QueryRun, OntologyRule, OntologyIndividual)
