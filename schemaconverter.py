import json
import os
from lxml import etree

# Helper function to convert snake_case or camelCase to PascalCase
def to_pascal_case(name):
    return name[0].upper() + name[1:]

# Function to process schema and move properties to definitions
def transform_schema(schema):
    definitions = {}

    # Remove the "$schema" key    
    schema.pop("$schema", None)
    
    # Replace the "id" key with "$id" and place it on the first row in the schema
    schema = {"$id": schema.pop("id")} | schema

    
    # Process "$id" key with proper naming
    schema["$id"] = schema["$id"].rsplit(":", 1)[-1] + "16"

    # Process "title" key with proper naming
    schema["title"] = schema["title"] + "16"

    
    def process_properties(properties):
        for key, property in list(properties.items()):
            if 'type' in property:
                # Handle object type properties
                if property['type'] == 'object':
                    # Create a PascalCase definition name with "Type" suffix
                    definition_name = to_pascal_case(key) 
                    definitions[definition_name] = property

                    # Replace the property with a reference to the new definition
                    properties[key] = { "$ref": f"#/definitions/{definition_name}" }

                    # Recursively process nested properties
                    if 'properties' in property:
                        process_properties(definitions[definition_name]['properties'])

                # Handle array type properties
                elif property['type'] == 'array':
                    if 'items' in property:
                        items = property['items']
                        if 'type' in items and items['type'] == 'object':
                            definition_name = to_pascal_case(key) 
                            definitions[definition_name] = items
                            process_properties(definitions[definition_name]['properties'])

                            # Replace the array items with a reference
                            property['items'] = { "$ref": f"#/definitions/{definition_name}" }
                        
                        # Handle nested arrays
                        elif items['type'] == 'array':
                            definition_name = to_pascal_case(key) 
                            definitions[definition_name] = items
                            property['items'] = { "$ref": f"#/definitions/{definition_name}" }
                            process_properties(definitions[definition_name])

                # Handle enum type properties
                elif 'enum' in property:
                    definition_name = to_pascal_case(key) 
                    definitions[definition_name] = property

                    properties[key] = { "$ref": f"#/definitions/{definition_name}" }

    # Process the top-level properties in the schema
    if 'properties' in schema:
        process_properties(schema['properties'])

    # Add the new definitions to the schema
    schema['definitions'] = definitions
    return schema

# Function to parse WSDL file to extract simple types with enum values and their corresponding complex types
def get_wsdl_enums(wsdl_file):
    tree = etree.parse(wsdl_file)
    root = tree.getroot()

    wsdl_enums = {}

    # Define WSDL namespaces for searching in the file
    namespaces = {
        'wsdl': 'http://schemas.xmlsoap.org/wsdl/',
        'xsd': 'http://www.w3.org/2001/XMLSchema',
        's': 'http://www.w3.org/2001/XMLSchema'  # Alias for simple types
    }

    # Search for simple types (enums)
    simple_types = root.xpath('//s:simpleType', namespaces=namespaces)
    
    # Step 1: Collect enums and their values
    for simple_type in simple_types:
        enum_name = simple_type.get('name')
        enum_values = simple_type.xpath('.//s:enumeration/@value', namespaces=namespaces)

        if enum_values:
            wsdl_enums[enum_name] = {'enum_values': enum_values, 'complex_types': []}

    # Step 2: Find complex types and associate with enums
    complex_types = root.xpath('//s:complexType', namespaces=namespaces)

    for complex_type in complex_types:
        complex_type_name = complex_type.get('name')
        elements = complex_type.xpath('.//s:element', namespaces=namespaces)

        for element in elements:
            element_name = element.get('name')
            element_type = element.get('type')

            if element_type and element_type.startswith('tns:'):
                element_type = element_type[4:]  # Remove 'tns:' prefix

            # If element_type is an enum, associate the complex type with it
            if element_type in wsdl_enums:
                wsdl_enums[element_type]['complex_types'].append({
                    'complex_type': complex_type_name,
                    'element_name': element_name
                })

    # Step 3: Check if any complex types are contained in other complex types
    for complex_type in complex_types:
        complex_type_name = complex_type.get('name')
        elements = complex_type.xpath('.//s:element', namespaces=namespaces)

        for element in elements:
            element_type = element.get('type')

            if element_type and element_type.startswith('tns:'):
                nested_complex_type = element_type[4:]

                # Check if the nested type is already associated with an enum and propagate the association
                for enum_key, enum_data in wsdl_enums.items():
                    for assoc in enum_data['complex_types']:
                        if assoc['complex_type'] == nested_complex_type:
                            wsdl_enums[enum_key]['complex_types'].append({
                                'complex_type': complex_type_name,
                                'element_name': element.get('name')
                            })

    return wsdl_enums


# Function to extract complex types and their elements
def get_complex_types(wsdl_file):
    tree = etree.parse(wsdl_file)
    root = tree.getroot()

    namespaces = {
        'wsdl': 'http://schemas.xmlsoap.org/wsdl/',
        'xsd': 'http://www.w3.org/2001/XMLSchema',
        's': 'http://www.w3.org/2001/XMLSchema'  # Alias for simple types
    }

    complex_types = {}
    complex_type_elements = root.xpath('//s:complexType', namespaces=namespaces)

    for complex_type in complex_type_elements:
        complex_type_name = complex_type.get('name')
        if "Request" not in complex_type_name and "Response" not in complex_type_name:
            elements = complex_type.xpath('.//s:element', namespaces=namespaces)
            complex_types[complex_type_name] = {
                'elements': [element.get('name') for element in elements]
            }
            #print(f"Found complex type {complex_type_name} with elements {complex_types[complex_type_name]['elements']}")

    # Remove duplicates
    complex_types = dict((k, v) for k, v in complex_types.items())
    return complex_types

# Function to update the JSON schema with correct naming for enums and complex types
def update_json_schema(json_schema, wsdl_enums, wsdl_complex_types):
    # Get schema definitions
    definitions = json_schema.get('definitions', {})

    # Process enums in JSON schema
    for schema_key, schema_value in definitions.items():
        if 'enum' in schema_value:
            json_enum = schema_value['enum']

            # Try to find matching WSDL enum

            for wsdl_enum_name, wsdl_enum_info in wsdl_enums.items():
                if sorted(json_enum) == sorted(wsdl_enum_info['enum_values']):
                    print(f"Matching enum found: {wsdl_enum_name} for JSON enum {schema_key}")
                    # Ensure complex types match the $id of the schema
                    json_id = json_schema.get('$id', '').rstrip('16')
                    complex_type_names = [ct['complex_type'] for ct in wsdl_enum_info['complex_types']]
                    
                    for ct in complex_type_names:
                        if ct in json_schema['properties'] or ct in json_schema['definitions'] or ct == json_id:
                            json_schema = json.dumps(json_schema).replace(schema_key, wsdl_enum_name + 'EnumType16')
                            json_schema = json.loads(json_schema)
                            print(f"JSON schema for {json_id} updated with enum {wsdl_enum_name} for {schema_key}.")
                            break
                        else:
                            continue
                    continue

    # Process complex types in JSON schema
    for schema_key, schema_value in definitions.items():
        if 'properties' in schema_value:
            prop_keys = list(schema_value['properties'].keys())

            # Match WSDL complex types to JSON properties
            for complex_type_name, complex_type_info in wsdl_complex_types.items():
                if sorted(prop_keys) == sorted(complex_type_info['elements']):
                    print(f"Matching complex type: {complex_type_name} for {schema_key}")

                    # Update complex type in schema
                    # Extract values of $id and title
                    schema_id = json_schema.get('$id', '')
                    schema_title = json_schema.get('title', '')

                    # Replace schema key with WSDL complex type name
                    json_schema = json.dumps(json_schema).replace(schema_key, complex_type_name + 'Type16')

                    # Replace $id and title values back into schema
                    json_schema = json.loads(json_schema)
                    json_schema['$id'] = schema_id
                    json_schema['title'] = schema_title
                    break

    return json_schema


# Extract enum types from WSDL
wsdl_file1 = 'OCPP_CentralSystemService_1.6.wsdl'
wsdl_file2 = 'OCPP_ChargePointService_1.6.wsdl'
wsdl_enums1 = get_wsdl_enums(wsdl_file1)
wsdl_enums2 = get_wsdl_enums(wsdl_file2)

# Combine the two dictionaries into one
wsdl_enums = {**wsdl_enums1, **wsdl_enums2}
print(f"wsdl enums are")
for k,v in wsdl_enums.items():
    print(f"{k}: {v}")

# Extract complex types from WSDL
wsdl_complex_types1 = get_complex_types(wsdl_file1)
wsdl_complex_types2 = get_complex_types(wsdl_file2)

# Combine the complex types into one dictionary
wsdl_complex_types = {**wsdl_complex_types1, **wsdl_complex_types2}


# Step 3: Update the JSON schema by replacing key names and definitions
# Get the list of schema files in the given folder
schema_files = [file for file in os.listdir() if file.endswith(".json")]

# Create a new folder to store the transformed schema files
if not os.path.exists("transformed_schemas"):
    os.mkdir("transformed_schemas")

# Loop through each schema file and transform it
for file in schema_files:
    # Read the schema from the file at the given path
    with open(file) as f:
        json_schema = json.load(f)

    # Transform the schema
    transformed_schema = transform_schema(json_schema)
    final_schema = update_json_schema(transformed_schema, wsdl_enums, wsdl_complex_types)

    # Write the transformed schema to a file in the new folder
    if "Response" not in file:
        transformed_file_name = os.path.splitext(file)[0] + "Request16.json"
    else:
        transformed_file_name = os.path.splitext(file)[0] + "16.json"
    with open(os.path.join("transformed_schemas", transformed_file_name), 'w') as f:
        json.dump(final_schema, f, indent=2)
